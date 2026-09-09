"""Command line entry point.

    python -m clearance_desk demo
    python -m clearance_desk run --title "Nightshift" --gcs gs://bucket/cut.mp4 \
        --script script.pdf --runtime 5640
    python -m clearance_desk rules
"""

from __future__ import annotations

import argparse
import sys

from .config import get_settings
from .demo import demo_items, demo_project
from .models import ProgressEvent, ProjectMeta, RiskTier, RunStatus
from .pipeline import ClearancePipeline
from .report import render_markdown
from .rules import rule_catalogue

_TIER_COLOUR = {
    RiskTier.BLOCKING: "\033[31m",
    RiskTier.HIGH: "\033[33m",
    RiskTier.MEDIUM: "\033[93m",
    RiskTier.LOW: "\033[36m",
    RiskTier.CLEARED: "\033[32m",
}
_RESET = "\033[0m"
_DIM = "\033[2m"

#: Set from --no-colour. Terminals that mangle ANSI (and piped output) need it.
_USE_COLOUR = True


def _c(code: str) -> str:
    return code if _USE_COLOUR else ""


def _echo(event: ProgressEvent) -> None:
    tint = "\033[31m" if event.kind == "error" else _DIM
    print(f"  {_c(tint)}{event.stage.value:<13}{_c(_RESET)} {event.message}", flush=True)


def _print_report(report) -> None:  # noqa: ANN001
    def tint(tier: RiskTier) -> str:
        return _c(_TIER_COLOUR[tier])

    reset = _c(_RESET)
    print()
    print("=" * 76)
    print(f" {report.project.title} — {report.project.cut_label}")
    print("=" * 76)
    print(report.summary or "")
    print()

    for entry in report.sorted_entries():
        verdict = entry.verdict
        if verdict is None:
            continue
        where = entry.item.timecode or entry.item.scene or "—"
        print(
            f" {tint(verdict.tier)}{verdict.tier.value.upper():<9}{reset} "
            f"{entry.item.research_subject()[:38]:<38} {where:<22} "
            f"{verdict.action.value.replace('_', ' ')}"
        )
        if entry.finding and entry.finding.unresolved:
            print(f" {_c(_DIM)}          ↳ {entry.finding.unresolved[0][:88]}{reset}")

    print()
    low, high = report.estimated_cost_range
    print(
        f" {len(report.entries)} items · {len(report.blocking_entries)} blocking · "
        f"${low:,.0f}–${high:,.0f} · {report.total_citations} sources cited · "
        f"E&O ready: {report.eo_ready}"
    )
    print()


#: Published Parallel list price per task run, in USD. Used only to warn the
#: operator before a billed action — never for accounting.
_PROCESSOR_PRICE_USD = {
    "lite": 0.005,
    "base": 0.010,
    "core": 0.025,
    "core2x": 0.050,
    "pro": 0.100,
    "ultra": 0.300,
    "ultra2x": 0.600,
    "ultra4x": 1.200,
    "ultra8x": 2.400,
}


def _estimate_demo_cost() -> tuple[float, int]:
    """Rough cost of one live demo pass, from the items that need research."""
    from .research import needs_research, processor_for

    settings = get_settings()
    subjects: dict[tuple[str, str], str] = {}
    for item in demo_items():
        if not needs_research(item):
            continue
        key = (item.category.value, item.research_subject().strip().lower())
        subjects.setdefault(key, processor_for(item, settings))

    total = 0.0
    for processor in subjects.values():
        total += _PROCESSOR_PRICE_USD.get(processor.replace("-fast", ""), 0.0)
    return total, len(subjects)


def _check_models() -> int:
    """Report which Gemini models this project and region can actually call."""
    from google import genai
    from google.genai import types

    settings = get_settings()
    if not settings.google_cloud_project:
        print("GOOGLE_CLOUD_PROJECT is not set.", file=sys.stderr)
        return 1

    client = genai.Client(
        vertexai=True,
        project=settings.google_cloud_project,
        location=settings.google_cloud_location,
    )
    candidates = [
        settings.spotter_model,
        settings.drafter_model,
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ]
    seen: set[str] = set()
    print(f"project={settings.google_cloud_project} region={settings.google_cloud_location}\n")
    ok = []
    for model in candidates:
        if not model or model in seen:
            continue
        seen.add(model)
        try:
            client.models.generate_content(
                model=model,
                contents="ok",
                config=types.GenerateContentConfig(max_output_tokens=1000),
            )
            print(f"  available    {model}")
            ok.append(model)
        except Exception as exc:  # noqa: BLE001
            reason = "not found in this project/region" if "404" in str(exc) else str(exc)[:70]
            print(f"  UNAVAILABLE  {model}  ({reason})")
    print(f"\nusable: {', '.join(ok) if ok else 'none'}")
    return 0 if ok else 1


def _record_demo(confirmed: bool) -> int:
    """Run the demo live once and store it for replay."""
    from .demo_cache import CACHE_PATH, save_demo_run

    settings = get_settings()
    if not settings.parallel_enabled:
        print(
            "Refusing to record: research is in fixture mode, so the recording "
            "would contain no live findings. Set PARALLEL_API_KEY and "
            "USE_FIXTURES=false.",
            file=sys.stderr,
        )
        return 1

    cost, calls = _estimate_demo_cost()
    print(f"This performs {calls} live Parallel task run(s), about ${cost:.2f}.")
    print(f"Recording to: {CACHE_PATH}")
    if not confirmed:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            print("Aborted; nothing was billed.")
            return 1

    events: list[ProgressEvent] = []

    def collect(event: ProgressEvent) -> None:
        events.append(event)
        _echo(event)

    report = ClearancePipeline().run(
        demo_project(), items=demo_items(), on_event=collect
    )
    if report.status is not RunStatus.COMPLETE:
        print(f"\nRun did not complete ({report.error}); not recording.", file=sys.stderr)
        return 1

    save_demo_run(report, events)
    _print_report(report)
    print(f"Recorded {len(report.entries)} items and {report.total_citations} citations.")
    print(f"Saved to {CACHE_PATH}")
    print("Redeploy to ship this recording with the service.")
    return 0


def _record_sample(sample_id: str | None, confirmed: bool) -> int:
    """Run one bundled sample live and store it for replay.

    Unlike the worked example, a sample starts from picture rather than from
    supplied items, so the picture pass runs too and the cost cannot be known
    until the spotter has said how much is in the minute. The estimate below is
    therefore a range, and it is stated before anything is billed.
    """
    from .cue_sheet import parse_cue_sheet
    from .samples import get_sample, load_samples, save_sample_recording

    settings = get_settings()
    if not settings.parallel_enabled:
        print(
            "Refusing to record: research is in fixture mode, so the recording "
            "would contain no live findings. Set PARALLEL_API_KEY and "
            "USE_FIXTURES=false.",
            file=sys.stderr,
        )
        return 1

    available = load_samples()
    if not available:
        print("No samples found. Is assets/samples present?", file=sys.stderr)
        return 1

    if sample_id in (None, "all"):
        targets = available
    else:
        one = get_sample(sample_id or "")
        if one is None:
            names = ", ".join(s.id for s in available)
            print(f"No sample {sample_id!r}. Available: {names}", file=sys.stderr)
            return 1
        targets = [one]

    print(f"About to record {len(targets)} sample(s) live:")
    for sample in targets:
        print(f"  - {sample.id} ({sample.title}, {sample.runtime_seconds or '?'}s)")
    print(
        "\nEach one runs the picture pass over the clip and then researches "
        "every published work it finds."
    )
    print(
        f"Expect roughly $0.30-$0.80 per sample, so about "
        f"${0.30 * len(targets):.2f}-${0.80 * len(targets):.2f} in total."
    )
    if not confirmed:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            print("Aborted; nothing was billed.")
            return 1

    failures = 0
    for sample in targets:
        print(f"\n=== {sample.id} : {sample.title} ===")
        events: list[ProgressEvent] = []

        def collect(event: ProgressEvent) -> None:
            events.append(event)
            _echo(event)

        cue_sheet = None
        cue_path = sample.cue_sheet_path
        if cue_path is not None and cue_path.is_file():
            try:
                cue_sheet = parse_cue_sheet(cue_path.read_bytes(), cue_path.name)
            except Exception as exc:  # noqa: BLE001
                print(f"  cue sheet unreadable ({exc}); continuing without it")

        project = ProjectMeta(
            title=sample.title,
            cut_label=(
                f"sample excerpt {sample.excerpt}"
                if sample.excerpt
                else "sample excerpt"
            ),
            runtime_seconds=sample.runtime_seconds,
        )
        report = ClearancePipeline().run(
            project,
            local_path=sample.video_path,
            cue_sheet=cue_sheet,
            on_event=collect,
        )
        if report.status is not RunStatus.COMPLETE:
            print(f"  did not complete ({report.error}); not recording.", file=sys.stderr)
            failures += 1
            continue

        path = save_sample_recording(sample, report, events)
        print(
            f"  recorded {len(report.entries)} item(s), "
            f"{report.total_citations} citation(s) -> {path}"
        )

    if failures:
        print(f"\n{failures} sample(s) failed.", file=sys.stderr)
    print("\nRedeploy to ship these recordings with the service.")
    return 1 if failures else 0


def _readjudicate(confirmed: bool) -> int:
    """Re-run the rules engine over every recorded run.

    Recordings are snapshots. Change a rule and they keep showing the old
    position, so the demo and the code quietly disagree — which is worse than
    either being wrong on its own.

    This costs nothing and calls nothing: adjudication is a pure function of
    the item, the finding and the project, so the recorded research is reused
    exactly as it was returned. Only the position is recomputed. Anything that
    needed the network — spotting, research, drafting — is left untouched.
    """
    from .demo_cache import CACHE_PATH, load_demo_run, save_demo_run
    from .rules import adjudicate
    from .samples import load_samples

    targets = [("worked example", CACHE_PATH)]
    for sample in load_samples():
        if sample.has_recording():
            targets.append((sample.id, sample.recording_path))
    targets = [(name, path) for name, path in targets if path and path.is_file()]

    if not targets:
        print("No recordings found.", file=sys.stderr)
        return 1

    print(f"Re-adjudicating {len(targets)} recording(s). Nothing is billed.")
    if not confirmed:
        reply = input("Proceed? [y/N] ").strip().lower()
        if reply not in {"y", "yes"}:
            print("Aborted.")
            return 1

    moved = 0
    for name, path in targets:
        loaded = load_demo_run(path)
        if loaded is None:
            print(f"  {name}: unreadable, skipped", file=sys.stderr)
            continue
        report, events = loaded

        reworded = positions = 0
        for entry in report.entries:
            before = entry.verdict
            after = adjudicate(entry.item, entry.finding, report.project)
            if before is not None and (
                before.tier != after.tier
                or before.action != after.action
                or before.eo_blocking != after.eo_blocking
            ):
                positions += 1
            elif before is not None and before.rationale != after.rationale:
                reworded += 1
            entry.verdict = after

        save_demo_run(report, events, path)
        moved += positions
        print(f"  {name}: {reworded} reworded, {positions} position(s) changed")

    if moved:
        print(
            "PLEASE NOTE: positions changed, and the recorded summary paragraph "
            "was written against the old ones. Re-record those runs for a true "
            "pass before showing them.",
            file=sys.stderr,
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clearance_desk", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Run the worked example")

    run = sub.add_parser("run", help="Run a clearance pass")
    run.add_argument("--title", required=True)
    run.add_argument("--cut", default="rough cut")
    run.add_argument("--gcs", default=None, help="gs:// URI of the cut")
    run.add_argument("--script", default=None, help="Path to a .pdf or .txt script")
    run.add_argument("--runtime", type=int, default=0, help="Runtime in seconds")
    run.add_argument(
        "--intent",
        default="worldwide, all media, in perpetuity",
        help="Rights being sought — drives fee estimates",
    )

    sub.add_parser("rules", help="Print the rule catalogue")
    sub.add_parser(
        "check-models", help="Report which Gemini models this project can call"
    )
    rec = sub.add_parser(
        "record-demo",
        help="Run the demo live once and record it for replay (BILLED)",
    )
    rec.add_argument(
        "--yes", action="store_true", help="Skip the cost confirmation prompt"
    )

    recs = sub.add_parser(
        "record-sample",
        help="Run a bundled sample live and record it for replay (BILLED)",
    )
    recs.add_argument(
        "sample",
        nargs="?",
        default=None,
        help="Sample id, or 'all' / omitted for every sample",
    )
    recs.add_argument(
        "--yes", action="store_true", help="Skip the cost confirmation prompt"
    )

    rej = sub.add_parser(
        "readjudicate",
        help="Re-run the rules over every recording (free; no API calls)",
    )
    rej.add_argument("--yes", action="store_true", help="Skip the prompt")

    for p in (sub.choices["demo"], sub.choices["run"]):
        p.add_argument("--markdown", action="store_true", help="Print the full report")
        p.add_argument("--no-colour", action="store_true")

    args = parser.parse_args(argv)

    # Windows defaults stdout to cp1252, which cannot encode the timecode
    # dashes, ellipses and arrows this output uses. Redirect to a file and it
    # raises mid-run. Force UTF-8 and degrade characters rather than fail.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # pragma: no cover - stream-dependent
                pass

    global _USE_COLOUR
    _USE_COLOUR = not getattr(args, "no_colour", False) and sys.stdout.isatty()

    if args.command == "rules":
        for rule in rule_catalogue():
            print(f"{rule['rule_id']:<10} [{rule['applies_to']}]")
            print(f"           {rule['description']}\n")
        return 0

    if args.command == "check-models":
        return _check_models()

    if args.command == "record-demo":
        return _record_demo(confirmed=args.yes)

    if args.command == "record-sample":
        return _record_sample(args.sample, confirmed=args.yes)

    if args.command == "readjudicate":
        return _readjudicate(confirmed=args.yes)

    settings = get_settings()
    print(
        f"{_c(_DIM)}research: "
        f"{'parallel (live)' if settings.parallel_enabled else 'fixtures (offline)'}"
        f" · spotter: {settings.spotter_model}{_c(_RESET)}\n"
    )

    pipeline = ClearancePipeline()

    if args.command == "demo":
        report = pipeline.run(demo_project(), items=demo_items(), on_event=_echo)
    else:
        script_text = None
        if args.script:
            from .spotter import read_script

            script_text = read_script(args.script)
        if not args.gcs and not script_text:
            print("Provide --gcs and/or --script.", file=sys.stderr)
            return 2
        report = pipeline.run(
            ProjectMeta(
                title=args.title,
                cut_label=args.cut,
                runtime_seconds=args.runtime or None,
                distribution_intent=args.intent,
            ),
            gcs_uri=args.gcs,
            script_text=script_text,
            on_event=_echo,
        )

    if args.markdown:
        print(render_markdown(report))
    else:
        _print_report(report)

    if report.error:
        print(f"error: {report.error}", file=sys.stderr)
        return 1
    return 0 if report.eo_ready else 3


if __name__ == "__main__":
    raise SystemExit(main())
