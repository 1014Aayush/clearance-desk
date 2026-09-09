"""Recorded demo run, replayed instead of re-researched.

Every live demo pass costs real money — roughly $0.48 at current processor
tiers — and a public URL with a "run the demo" button charges the *owner* once
per visitor, not once per demo. A shared link is therefore a slow drain on a
fixed budget, and the failure mode is the worst possible one: the account
empties and the submission URL demonstrates a dead app.

So the demo runs live once, and every visitor afterwards sees that recording.

This is a replay, not a simulation. The report served is the exact output of a
real pass — real Parallel research, real citations, real adjudication — and it
is labelled as a replay wherever it is shown, with the date it was recorded and
the identifier of the run it came from. Passing a recording off as a live call
would be dishonest; refusing to record one would be wasteful.

Record a fresh one with::

    python -m clearance_desk record-demo
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .models import ClearanceReport, ProgressEvent

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent / "data" / "demo_run.json"


def save_demo_run(
    report: ClearanceReport,
    events: list[ProgressEvent],
    path: Path | None = None,
) -> Path:
    """Persist a completed live run for later replay."""
    path = path or CACHE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "report": report.model_dump(mode="json"),
        "events": [e.model_dump(mode="json") for e in events],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    logger.info("recorded demo run %s to %s", report.run_id, path)
    return path


def load_demo_run(
    path: Path | None = None,
) -> tuple[ClearanceReport, list[ProgressEvent]] | None:
    """Load the recorded run, or None if there isn't one."""
    path = path or CACHE_PATH
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        report = ClearanceReport.model_validate(payload["report"])
        events = [ProgressEvent.model_validate(e) for e in payload.get("events", [])]
    except Exception:  # noqa: BLE001
        logger.exception("recorded demo run at %s is unreadable; ignoring", path)
        return None
    return report, events


def has_recording(path: Path | None = None) -> bool:
    return (path or CACHE_PATH).exists()


def as_replay(
    report: ClearanceReport,
    events: list[ProgressEvent],
    new_run_id: str,
) -> tuple[ClearanceReport, list[ProgressEvent]]:
    """Re-badge a recorded run under a fresh identifier.

    The provenance of the original is preserved on the copy so the interface
    can say plainly that this is a replay and when it was really run.
    """
    replay = report.model_copy(deep=True)
    replay.replay_of = report.run_id
    replay.replay_recorded_at = report.completed_at or report.created_at
    replay.run_id = new_run_id

    replayed_events = []
    for event in events:
        copy = event.model_copy(deep=True)
        copy.run_id = new_run_id
        replayed_events.append(copy)
    return replay, replayed_events
