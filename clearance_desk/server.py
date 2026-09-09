"""HTTP surface for Clearance Desk.

Runs are started in a background thread and progress is polled, rather than
held open on a single long request. A deep research pass over a feature's worth
of material takes minutes, and a reviewer who refreshes the page should not
lose it.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio_id import ffmpeg_available
from .config import get_settings
from .cue_sheet import CueSheet, parse_cue_sheet
from .demo import demo_items, demo_project
from .demo_cache import as_replay, has_recording, load_demo_run
from .drafter import next_actions
from .models import ClearanceReport, ProgressEvent, ProjectMeta, RiskItem, RunStatus
from .pipeline import ClearancePipeline, RunStore
from .report import render_markdown
from .report_html import render_html
from .samples import get_sample, load_sample_recording, load_samples
from .rules import rule_catalogue
from .uploads import INLINE_LIMIT_BYTES, UploadStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Clearance Desk",
    description=(
        "Agentic rights clearance for film and television. Gemini spots "
        "clearable material in a cut; Parallel researches the chain of title; "
        "a deterministic rules engine produces the E&O clearance report."
    ),
    version="0.1.0",
)

store = RunStore()
uploads = UploadStore()
cue_sheets: dict[str, "CueSheet"] = {}
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RunRequest(BaseModel):
    project: ProjectMeta
    gcs_uri: str | None = Field(
        default=None, description="gs:// URI of the cut to analyse"
    )
    upload_id: str | None = Field(
        default=None,
        description="Handle returned by POST /api/uploads",
    )
    cue_sheet_id: str | None = Field(
        default=None,
        description="Handle returned by POST /api/cue-sheets",
    )
    script_text: str | None = None
    items: list[RiskItem] | None = Field(
        default=None,
        description="Pre-spotted items, bypassing the picture pass",
    )


class UploadAccepted(BaseModel):
    upload_id: str
    filename: str
    size_bytes: int
    location: str
    duration_seconds: int | None = None


class RunAccepted(BaseModel):
    run_id: str
    status: RunStatus


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _execute(run_id: str, request: RunRequest) -> None:
    pipeline = ClearancePipeline()

    def on_event(event: ProgressEvent) -> None:
        store.append_event(event)

    gcs_uri = request.gcs_uri
    local_path = None
    if request.upload_id:
        # Resolved server-side: the client never supplies a filesystem path.
        gcs_uri, local_path, _ = uploads.resolve(request.upload_id)

    try:
        report = pipeline.run(
            request.project,
            gcs_uri=gcs_uri,
            local_path=local_path,
            script_text=request.script_text,
            items=request.items,
            cue_sheet=cue_sheets.get(request.cue_sheet_id or ""),
            run_id=run_id,
            on_event=on_event,
        )
    except Exception as exc:  # noqa: BLE001 - never lose the run
        logger.exception("Run %s crashed", run_id)
        report = store.get(run_id) or ClearanceReport(
            run_id=run_id, project=request.project
        )
        report.status = RunStatus.FAILED
        report.error = f"{type(exc).__name__}: {exc}"
        store.append_event(
            ProgressEvent(
                run_id=run_id,
                stage=RunStatus.FAILED,
                message=report.error,
                kind="error",
            )
        )
    store.set_report(run_id, report)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health() -> dict[str, object]:
    settings = get_settings()
    return {
        "status": "ok",
        "config": settings.describe(),
        "parallel_live": settings.parallel_enabled,
        "vertex_configured": settings.vertex_configured,
        "audio_id_enabled": settings.audio_id_enabled,
        "ffmpeg_available": ffmpeg_available(),
        "demo_recording_available": has_recording(),
        "upload_target": (
            f"gs://{settings.gcs_bucket}" if settings.gcs_bucket else "local disk"
        ),
        "max_upload_mb": None if settings.gcs_bucket else INLINE_LIMIT_BYTES // 1_000_000,
        "live_runs_per_day": settings.max_live_runs_per_day or None,
        "live_runs_spent_today": _budget().spent_today(),
    }


@app.get("/api/rules")
def rules() -> dict[str, object]:
    """The full rule catalogue, so a verdict can be argued with specifically."""
    return {"rules": rule_catalogue()}


@app.post("/api/uploads", response_model=UploadAccepted, status_code=201)
async def create_upload(file: UploadFile = File(...)) -> UploadAccepted:
    """Stage a cut for analysis and return a handle to it."""
    try:
        upload = uploads.accept(file.filename or "cut.mp4", file.file)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("upload failed")
        raise HTTPException(status_code=500, detail=f"upload failed: {exc}") from exc
    finally:
        await file.close()

    return UploadAccepted(
        upload_id=upload.handle,
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        location=upload.location,
        duration_seconds=upload.duration_seconds,
    )


@app.post("/api/cue-sheets", status_code=201)
async def create_cue_sheet(file: UploadFile = File(...)) -> dict[str, object]:
    """Parse a music cue sheet and hold it for a run.

    The cue sheet is the production's own declaration of what music is in the
    cut, so it outranks anything inferred from the audio.
    """
    try:
        raw = await file.read()
        sheet = parse_cue_sheet(raw, file.filename or "cue_sheet.csv")
    except Exception as exc:  # noqa: BLE001
        logger.exception("cue sheet parse failed")
        raise HTTPException(status_code=400, detail=f"could not read: {exc}") from exc
    finally:
        await file.close()

    if not sheet.entries:
        raise HTTPException(
            status_code=400,
            detail="; ".join(sheet.warnings) or "no cues found in the file",
        )

    cue_sheet_id = uuid.uuid4().hex[:12]
    with _lock:
        cue_sheets[cue_sheet_id] = sheet

    return {
        "cue_sheet_id": cue_sheet_id,
        "cues": len(sheet.entries),
        "placed": len(sheet.placed),
        "library_cues": sum(1 for e in sheet.entries if e.is_library),
        "warnings": sheet.warnings,
        "preview": [
            {
                "title": e.work_title(),
                "publisher": e.publisher,
                "usage": e.usage,
                "library": e.is_library,
                "start": e.start_seconds,
            }
            for e in sheet.entries[:8]
        ],
    }


class _LiveRunBudget:
    """A hard stop on billed runs, enforced where it cannot be walked past.

    The confirmation dialog in the browser is decoration. It stops an honest
    tester clicking around; it does nothing at all about a loop against the
    API, and this deployment is public and unauthenticated. Research is billed
    per distinct work against a fixed prepaid balance, so roughly forty
    unattended requests would empty the account and leave the submission URL
    demonstrating a dead application — the precise outcome the replay design
    elsewhere in this file exists to prevent.

    Replays are never counted. They cost nothing, they are what the bundled
    samples are for, and a visitor clicking through all eight should never be
    told the tool is out of budget.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day: date | None = None
        self._spent = 0

    def take(self, limit: int) -> bool:
        """Claim one billed run, or refuse. ``limit <= 0`` means no cap."""
        if limit <= 0:
            return True
        today = datetime.now(timezone.utc).date()
        with self._lock:
            if self._day != today:
                self._day, self._spent = today, 0
            if self._spent >= limit:
                return False
            self._spent += 1
            return True

    def spent_today(self) -> int:
        today = datetime.now(timezone.utc).date()
        with self._lock:
            return self._spent if self._day == today else 0


_live_budget = _LiveRunBudget()


class _DurableBudget:
    """The same count, kept in Cloud Storage so it outlives the container.

    The in-process counter resets on every deploy and on every cold start, and
    this service scales to zero — so an unattended caller who waits out an idle
    timeout gets a fresh allowance, and the daily cap is not really daily.
    Holding the number in one small object fixes that.

    Writes use a generation precondition, which is Cloud Storage's
    compare-and-swap: the write only lands if nobody else has written since we
    read. Two requests arriving together therefore cannot both spend the last
    run — one wins, the other retries against the new value and is refused.
    """

    OBJECT = "live-run-budget.json"

    def __init__(self, bucket: str, project: str = "") -> None:
        self.bucket = bucket
        self.project = project
        self._blob_ref = None

    def _blob(self):
        if self._blob_ref is None:
            from google.cloud import storage

            client = storage.Client(project=self.project or None)
            self._blob_ref = client.bucket(self.bucket).blob(self.OBJECT)
        return self._blob_ref

    def _today(self) -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def take(self, limit: int) -> bool:
        if limit <= 0:
            return True
        from google.api_core import exceptions as gexc

        blob = self._blob()
        for _ in range(6):
            try:
                raw = blob.download_as_bytes()
                generation = blob.generation
                state = json.loads(raw.decode("utf-8"))
            except gexc.NotFound:
                # No object yet. Generation 0 means "only if it still does not
                # exist", so a racing creator loses rather than overwrites.
                generation, state = 0, {"day": self._today(), "spent": 0}
            except Exception:  # noqa: BLE001
                logger.exception(
                    "durable budget unreadable; falling back to the in-process "
                    "counter for this request"
                )
                return _live_budget.take(limit)

            if state.get("day") != self._today():
                state = {"day": self._today(), "spent": 0}
            if int(state.get("spent", 0)) >= limit:
                return False

            state["spent"] = int(state.get("spent", 0)) + 1
            try:
                blob.upload_from_string(
                    json.dumps(state),
                    content_type="application/json",
                    if_generation_match=generation,
                )
                return True
            except gexc.PreconditionFailed:
                continue  # somebody else wrote; re-read and decide again
            except Exception:  # noqa: BLE001
                logger.exception(
                    "durable budget unwritable; falling back to the in-process "
                    "counter for this request"
                )
                return _live_budget.take(limit)

        # Sustained contention on a one-instance service means something is
        # hammering it, which is the case this guard exists for.
        logger.warning("durable budget contended out; refusing the run")
        return False

    def spent_today(self) -> int:
        try:
            state = json.loads(self._blob().download_as_bytes().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return 0
        return int(state.get("spent", 0)) if state.get("day") == self._today() else 0


_durable_budget: _DurableBudget | None = None


def _budget():
    """The in-process counter, unless a bucket is configured to outlive it."""
    global _durable_budget
    settings = get_settings()
    if not settings.budget_bucket:
        return _live_budget
    if _durable_budget is None or _durable_budget.bucket != settings.budget_bucket:
        _durable_budget = _DurableBudget(
            settings.budget_bucket, settings.google_cloud_project
        )
    return _durable_budget


@app.post("/api/runs", response_model=RunAccepted, status_code=202)
def create_run(request: RunRequest, background: BackgroundTasks) -> RunAccepted:
    # Every billed path in this file funnels through here — an uploaded cut, a
    # sample forced live, the worked example forced live — so this is the one
    # place the budget has to hold.
    settings = get_settings()
    if not settings.use_fixtures and not _budget().take(
        settings.max_live_runs_per_day
    ):
        raise HTTPException(
            status_code=429,
            detail=(
                f"This deployment serves at most "
                f"{settings.max_live_runs_per_day} billed run(s) per day, and "
                "today's are spent. Nothing is broken: every bundled sample "
                "still replays a real recorded pass, with its live findings "
                "and citations intact, for free. Run the tool from source to "
                "research your own footage without this limit."
            ),
        )

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    with _lock:
        store.create(
            run_id,
            ClearanceReport(
                run_id=run_id, project=request.project, status=RunStatus.PENDING
            ),
        )
    background.add_task(_execute, run_id, request)
    return RunAccepted(run_id=run_id, status=RunStatus.PENDING)


@app.post("/api/runs/demo", response_model=RunAccepted, status_code=202)
def create_demo_run(background: BackgroundTasks, live: bool = False) -> RunAccepted:
    """Serve the worked example.

    By default this replays a recorded live run. Live research is billed per
    request against a fixed budget, and a public demo button would otherwise
    charge the owner once per visitor — draining the account and leaving the
    submission URL demonstrating a dead app.

    Pass ``?live=true`` to force a real pass.
    """
    if not live:
        recorded = load_demo_run()
        if recorded is not None:
            report, events = recorded
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            replay, replay_events = as_replay(report, events, run_id)
            with _lock:
                store.create(run_id, replay)
                for event in replay_events:
                    store.append_event(event)
                store.set_report(run_id, replay)
            return RunAccepted(run_id=run_id, status=replay.status)

    return create_run(
        RunRequest(project=demo_project(), items=demo_items()), background
    )


# ---------------------------------------------------------------------------
# Bundled samples
# ---------------------------------------------------------------------------


@app.get("/api/samples")
def list_samples() -> dict[str, object]:
    """The cuts that ship with the application.

    A tester with no footage of their own is the common case, and asking them
    to go and find a video before the tool will say anything is a bad first
    minute. These are short public-domain excerpts chosen for density.
    """
    return {"samples": [sample.as_dict() for sample in load_samples()]}


@app.get("/api/samples/{sample_id}/video")
def get_sample_video(sample_id: str) -> FileResponse:
    """Serve a bundled cut, so the tester can watch what they are about to run."""
    sample = get_sample(sample_id)
    if sample is None or not sample.has_video():
        raise HTTPException(status_code=404, detail=f"no sample {sample_id!r}")
    return FileResponse(sample.video_path, media_type="video/mp4")


@app.get("/api/samples/{sample_id}/poster")
def get_sample_poster(sample_id: str) -> FileResponse:
    """A frame from the clip, so the gallery shows the cut rather than a name."""
    sample = get_sample(sample_id)
    if sample is None or not sample.has_poster():
        raise HTTPException(status_code=404, detail=f"no poster for {sample_id!r}")
    return FileResponse(sample.poster_path, media_type="image/jpeg")


@app.post("/api/runs/sample/{sample_id}", response_model=RunAccepted, status_code=202)
def create_sample_run(
    sample_id: str, background: BackgroundTasks, live: bool = False
) -> RunAccepted:
    """Run a bundled sample.

    Replays that sample's recorded pass by default, for the same reason the
    worked example does: spotting and research are both billed per request, so
    a sample that ran live on every visit would charge the account hosting this
    page once per tester. ``?live=true`` forces a real pass.
    """
    sample = get_sample(sample_id)
    if sample is None:
        raise HTTPException(status_code=404, detail=f"no sample {sample_id!r}")

    if not live:
        recorded = load_sample_recording(sample)
        if recorded is not None:
            report, events = recorded
            run_id = f"run_{uuid.uuid4().hex[:12]}"
            replay, replay_events = as_replay(report, events, run_id)
            with _lock:
                store.create(run_id, replay)
                for event in replay_events:
                    store.append_event(event)
                store.set_report(run_id, replay)
            return RunAccepted(run_id=run_id, status=replay.status)

        # Falling through to a live pass here would bill the caller for a
        # request that did not ask to be billed — the exact surprise the
        # confirmation in the UI exists to prevent. Spending must be explicit
        # at the API too, so refuse and say what to pass instead.
        raise HTTPException(
            status_code=409,
            detail=(
                f"sample {sample.id!r} has no recorded pass yet. Record one with "
                f"`python -m clearance_desk record-sample {sample.id}`, or pass "
                f"?live=true to research it now — which is billed per request."
            ),
        )

    if not sample.has_video():
        raise HTTPException(
            status_code=404, detail=f"sample {sample_id!r} has no video on disk"
        )

    # Staged through the same path as an uploaded cut, so a sample run and a
    # tester's own run differ in nothing but where the file came from.
    with sample.video_path.open("rb") as handle:
        upload = uploads.accept(sample.video, handle)

    cue_sheet_id: str | None = None
    cue_path = sample.cue_sheet_path
    if cue_path is not None and cue_path.is_file():
        try:
            sheet = parse_cue_sheet(cue_path.read_bytes(), cue_path.name)
        except Exception:  # noqa: BLE001 - a bad sheet must not sink the run
            logger.exception("could not read cue sheet for sample %s", sample.id)
        else:
            if sheet.entries:
                cue_sheet_id = uuid.uuid4().hex[:12]
                with _lock:
                    cue_sheets[cue_sheet_id] = sheet

    project = ProjectMeta(
        title=sample.title,
        cut_label=f"sample excerpt {sample.excerpt}" if sample.excerpt else "sample excerpt",
        runtime_seconds=upload.duration_seconds or sample.runtime_seconds,
        distribution_intent="worldwide, all media, in perpetuity",
        territories=["worldwide"],
    )
    return create_run(
        RunRequest(
            project=project, upload_id=upload.handle, cue_sheet_id=cue_sheet_id
        ),
        background,
    )


@app.get("/api/runs")
def list_runs() -> dict[str, object]:
    return {
        "runs": [
            {
                "run_id": r.run_id,
                "title": r.project.title,
                "cut": r.project.cut_label,
                "status": r.status,
                "created_at": r.created_at,
                "items": len(r.entries),
                "blocking": len(r.blocking_entries),
            }
            for r in store.list_runs()
        ]
    }


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, object]:
    report = store.get(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")

    low, high = report.estimated_cost_range
    return {
        "report": report.model_dump(mode="json"),
        "rollup": {
            "eo_ready": report.eo_ready,
            "tier_counts": report.tier_counts,
            "total_citations": report.total_citations,
            "cost_low": low,
            "cost_high": high,
            "longest_lead_days": report.longest_lead_time_days,
            "blocking_count": len(report.blocking_entries),
            "next_actions": next_actions(report),
        },
        "entries": [e.model_dump(mode="json") for e in report.sorted_entries()],
    }


@app.get("/api/runs/{run_id}/events")
def get_events(run_id: str, since: int = 0) -> dict[str, object]:
    if store.get(run_id) is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = store.events(run_id, since=since)
    report = store.get(run_id)
    return {
        "events": [e.model_dump(mode="json") for e in events],
        "next_cursor": since + len(events),
        "status": report.status if report else RunStatus.PENDING,
    }


@app.get("/api/runs/{run_id}/report.md", response_class=PlainTextResponse)
def get_markdown(run_id: str) -> str:
    report = store.get(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return render_markdown(report)


@app.get("/api/runs/{run_id}/report.html", response_class=HTMLResponse)
def get_html_report(run_id: str) -> str:
    """The printable clearance report — the artefact that leaves the building."""
    report = store.get(run_id)
    if report is None:
        raise HTTPException(status_code=404, detail="run not found")
    return render_html(report)


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> FileResponse:
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="UI not built")
    return FileResponse(index_file)


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host="0.0.0.0", port=settings.port)


if __name__ == "__main__":
    main()
