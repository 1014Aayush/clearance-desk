"""End-to-end orchestration of a clearance pass.

    spot  →  research  →  adjudicate  →  draft

The stage boundaries are deliberate. Spotting is the only stage that looks at
the picture, research is the only stage that touches the network, adjudication
is the only stage that forms a legal position, and it is pure. A reviewer
disputing the report can therefore be pointed at exactly one stage rather than
at "the AI".

Failures are contained per item. A research call that times out on one song
produces one item marked unresearched — which the rules engine then refuses to
clear — rather than a failed run. On a delivery deadline, a partial report with
honest gaps is worth considerably more than an exception.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterable

if TYPE_CHECKING:
    from .cue_sheet import CueSheet

from .config import Settings, get_settings
from .drafter import Drafter, entry_is_actionable
from .models import (
    ClearanceEntry,
    ClearanceReport,
    ProgressEvent,
    ProjectMeta,
    RightsFinding,
    RiskItem,
    RunStatus,
)
from .research import ResearchProvider, get_research_provider, research_items
from .rules import adjudicate

logger = logging.getLogger(__name__)

EventSink = Callable[[ProgressEvent], None]


class ClearancePipeline:
    """Runs a clearance pass and reports progress as it goes."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        spotter=None,  # noqa: ANN001 - duck-typed for testing
        provider: ResearchProvider | None = None,
        drafter: Drafter | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._spotter = spotter
        self._provider = provider
        self._drafter = drafter or Drafter(self.settings)

    # -- lazily constructed collaborators -----------------------------------

    def _get_spotter(self):  # noqa: ANN202
        if self._spotter is None:
            from .spotter import Spotter

            self._spotter = Spotter(self.settings)
        return self._spotter

    def _get_provider(self) -> ResearchProvider:
        if self._provider is None:
            self._provider = get_research_provider(self.settings)
        return self._provider

    # -- the run ------------------------------------------------------------

    def run(
        self,
        project: ProjectMeta,
        *,
        gcs_uri: str | None = None,
        local_path: str | Path | None = None,
        script_text: str | None = None,
        items: Iterable[RiskItem] | None = None,
        cue_sheet: "CueSheet | None" = None,
        run_id: str | None = None,
        on_event: EventSink | None = None,
    ) -> ClearanceReport:
        run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        report = ClearanceReport(
            run_id=run_id,
            project=project,
            status=RunStatus.PENDING,
            spotter_model=self.settings.spotter_model,
        )

        def emit(
            stage: RunStatus, message: str, *, kind: str = "stage", **detail
        ) -> None:
            report.status = stage
            if on_event is None:
                return
            try:
                on_event(
                    ProgressEvent(
                        run_id=run_id,
                        stage=stage,
                        message=message,
                        kind=kind,  # type: ignore[arg-type]
                        detail=detail,
                    )
                )
            except Exception:  # noqa: BLE001
                # Progress reporting is a display concern and must never be
                # able to fail a run. A console that cannot encode an arrow, or
                # a disconnected browser, previously destroyed minutes of
                # already-billed research on its way out.
                logger.warning("progress sink raised; continuing", exc_info=True)

        try:
            spotted = self._stage_spot(
                report,
                project,
                gcs_uri=gcs_uri,
                local_path=local_path,
                script_text=script_text,
                items=items,
                emit=emit,
            )
            if not spotted:
                report.status = RunStatus.COMPLETE
                report.completed_at = datetime.now(timezone.utc)
                report.summary = (
                    "No clearable material was identified. Confirm the correct "
                    "cut and script were supplied before relying on this."
                )
                emit(RunStatus.COMPLETE, report.summary, kind="done")
                return report

            self._stage_cue_sheet(spotted, cue_sheet, emit=emit)
            self._stage_identify(
                report, spotted, gcs_uri=gcs_uri, local_path=local_path, emit=emit
            )
            findings = self._stage_research(report, project, spotted, emit=emit)
            self._stage_adjudicate(report, project, spotted, findings, emit=emit)
            self._stage_draft(report, project, emit=emit)

            report.status = RunStatus.COMPLETE
            report.completed_at = datetime.now(timezone.utc)
            emit(
                RunStatus.COMPLETE,
                report.summary or "Clearance pass complete.",
                kind="done",
                eo_ready=report.eo_ready,
                blocking=len(report.blocking_entries),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Clearance run %s failed", run_id)
            report.status = RunStatus.FAILED
            report.error = f"{type(exc).__name__}: {exc}"
            report.completed_at = datetime.now(timezone.utc)
            emit(RunStatus.FAILED, report.error, kind="error")

        return report

    # -- stages -------------------------------------------------------------

    def _stage_spot(
        self,
        report: ClearanceReport,
        project: ProjectMeta,
        *,
        gcs_uri: str | None,
        local_path: str | Path | None,
        script_text: str | None,
        items: Iterable[RiskItem] | None,
        emit,  # noqa: ANN001
    ) -> list[RiskItem]:
        started = time.perf_counter()

        if items is not None:
            spotted = list(items)
            emit(
                RunStatus.SPOTTING,
                f"Loaded {len(spotted)} supplied item(s).",
                count=len(spotted),
            )
            report.stage_timings_ms["spot"] = _elapsed_ms(started)
            return spotted

        from .spotter import merge_sources

        groups: list[list[RiskItem]] = []

        if gcs_uri or local_path:
            emit(
                RunStatus.SPOTTING,
                f"Watching {project.cut_label} for third-party material…",
            )
            video_items = self._get_spotter().spot_video(
                project, gcs_uri=gcs_uri, local_path=local_path
            )
            emit(
                RunStatus.SPOTTING,
                f"{len(video_items)} item(s) observed in picture.",
                count=len(video_items),
            )
            groups.append(video_items)

        if script_text:
            emit(RunStatus.SPOTTING, "Reading the script…")
            script_items = self._get_spotter().spot_script(project, script_text)
            emit(
                RunStatus.SPOTTING,
                f"{len(script_items)} item(s) flagged in the script.",
                count=len(script_items),
            )
            groups.append(script_items)

        merged = merge_sources(*groups) if groups else []
        report.stage_timings_ms["spot"] = _elapsed_ms(started)
        return merged

    def _stage_cue_sheet(
        self,
        items: list[RiskItem],
        cue_sheet: "CueSheet | None",
        *,
        emit,  # noqa: ANN001
    ) -> None:
        """Reconcile spotted music against the production's own cue sheet.

        Runs before acoustic identification, so a cue the production has
        already declared is never sent for a paid lookup.
        """
        if cue_sheet is None or not cue_sheet.entries:
            return

        from .cue_sheet import apply_cue_sheet

        result = apply_cue_sheet(items, cue_sheet)
        parts = []
        if result.named:
            parts.append(f"{result.named} cue(s) named")
        if result.marked_library:
            parts.append(f"{result.marked_library} confirmed as library music")
        if result.added:
            parts.append(f"{result.added} cue(s) not seen in picture added")
        emit(
            RunStatus.SPOTTING,
            "Cue sheet applied: " + (", ".join(parts) if parts else "no changes"),
            named=result.named,
            library=result.marked_library,
            added=result.added,
        )
        for warning in cue_sheet.warnings:
            emit(RunStatus.SPOTTING, f"Cue sheet: {warning}")

    def _stage_identify(
        self,
        report: ClearanceReport,
        items: list[RiskItem],
        *,
        gcs_uri: str | None,
        local_path: str | Path | None,
        emit,  # noqa: ANN001
    ) -> None:
        """Name unnamed music before research, so research has a subject.

        Runs between spotting and research deliberately: a cue identified here
        becomes a work research can look up, turning a dead end into a normal
        two-sided music clearance.
        """
        from .audio_id import identify_music, needs_identification

        candidates = [i for i in items if needs_identification(i)]
        if not candidates:
            return

        # Fingerprinting needs the media itself. A gs:// URI is not readable by
        # ffmpeg without credentials, so only local media is sampled for now.
        source = local_path
        if source is None:
            emit(
                RunStatus.SPOTTING,
                f"{len(candidates)} unnamed cue(s); acoustic identification "
                "needs local media, skipping.",
            )
            return

        started = time.perf_counter()
        emit(
            RunStatus.SPOTTING,
            f"Identifying {len(candidates)} unnamed music cue(s)…",
            count=len(candidates),
        )
        try:
            matches = identify_music(items, source, settings=self.settings)
        except Exception:  # noqa: BLE001 - a failed lookup must not fail the run
            logger.exception("acoustic identification failed")
            matches = {}

        for item_id, match in matches.items():
            item = next((i for i in items if i.id == item_id), None)
            emit(
                RunStatus.SPOTTING,
                f"{item.timecode if item else item_id} identified as "
                f"{match.as_work_title()}",
                kind="item",
                item_id=item_id,
            )
        if not matches:
            emit(
                RunStatus.SPOTTING,
                "No cues matched a released recording — likely original score.",
            )
        report.stage_timings_ms["identify"] = _elapsed_ms(started)

    def _stage_research(
        self,
        report: ClearanceReport,
        project: ProjectMeta,
        items: list[RiskItem],
        *,
        emit,  # noqa: ANN001
    ) -> dict[str, RightsFinding]:
        started = time.perf_counter()
        provider = self._get_provider()
        report.research_provider = provider.name

        emit(
            RunStatus.RESEARCHING,
            f"Researching chain of title for {len(items)} item(s) via "
            f"{provider.name}…",
            provider=provider.name,
        )

        seen: set[str] = set()

        def on_result(item: RiskItem, finding: RightsFinding) -> None:
            if item.id in seen:
                return
            seen.add(item.id)
            if not finding.researched and finding.provider == "skipped":
                return
            holder = finding.holders[0].name if finding.holders else "no owner found"
            emit(
                RunStatus.RESEARCHING,
                f"{item.research_subject()} → {holder}",
                kind="item",
                item_id=item.id,
                citations=finding.citation_count,
                confidence=finding.confidence.value,
                unresolved=len(finding.unresolved),
            )

        findings = research_items(items, project, provider, on_result=on_result)
        report.stage_timings_ms["research"] = _elapsed_ms(started)
        return findings

    def _stage_adjudicate(
        self,
        report: ClearanceReport,
        project: ProjectMeta,
        items: list[RiskItem],
        findings: dict[str, RightsFinding],
        *,
        emit,  # noqa: ANN001
    ) -> None:
        started = time.perf_counter()
        emit(RunStatus.ADJUDICATING, "Applying clearance rules…")

        entries: list[ClearanceEntry] = []
        for item in items:
            finding = findings.get(item.id)
            verdict = adjudicate(item, finding, project)
            entries.append(
                ClearanceEntry(item=item, finding=finding, verdict=verdict)
            )

        report.entries = entries
        blocking = len(report.blocking_entries)
        emit(
            RunStatus.ADJUDICATING,
            f"{len(entries)} item(s) adjudicated; {blocking} blocking.",
            blocking=blocking,
            tiers=report.tier_counts,
        )
        report.stage_timings_ms["adjudicate"] = _elapsed_ms(started)

    def _stage_draft(
        self,
        report: ClearanceReport,
        project: ProjectMeta,
        *,
        emit,  # noqa: ANN001
    ) -> None:
        started = time.perf_counter()
        actionable = [e for e in report.entries if entry_is_actionable(e)]
        emit(
            RunStatus.DRAFTING,
            f"Drafting {len(actionable)} rights request(s)…",
            count=len(actionable),
        )

        for entry in actionable:
            if entry.verdict is None:
                continue
            entry.outreach_draft = self._drafter.draft_outreach(
                entry.item, entry.finding, entry.verdict, project
            )

        report.summary = self._drafter.summarise(report)
        report.stage_timings_ms["draft"] = _elapsed_ms(started)


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


# ---------------------------------------------------------------------------
# Run store
# ---------------------------------------------------------------------------


class RunStore:
    """In-process store of runs and their event streams.

    Deliberately simple. A production deployment would put this in Firestore;
    for a single Cloud Run instance driving a review session, memory is the
    right amount of machinery.
    """

    def __init__(self) -> None:
        self._reports: dict[str, ClearanceReport] = {}
        self._events: dict[str, list[ProgressEvent]] = {}

    def create(self, run_id: str, report: ClearanceReport) -> None:
        self._reports[run_id] = report
        self._events.setdefault(run_id, [])

    def append_event(self, event: ProgressEvent) -> None:
        self._events.setdefault(event.run_id, []).append(event)

    def set_report(self, run_id: str, report: ClearanceReport) -> None:
        self._reports[run_id] = report

    def get(self, run_id: str) -> ClearanceReport | None:
        return self._reports.get(run_id)

    def events(self, run_id: str, since: int = 0) -> list[ProgressEvent]:
        return self._events.get(run_id, [])[since:]

    def list_runs(self) -> list[ClearanceReport]:
        return sorted(
            self._reports.values(), key=lambda r: r.created_at, reverse=True
        )
