"""Tests for the recorded-demo replay path.

The commercial point of this feature is that a public demo button must not bill
the owner once per visitor. The integrity point is that a replay must never be
mistaken for a live call. Both are tested.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clearance_desk.config import Settings
from clearance_desk.demo import demo_items, demo_project
from clearance_desk.demo_cache import (
    as_replay,
    has_recording,
    load_demo_run,
    save_demo_run,
)
from clearance_desk.drafter import deterministic_summary, template_draft
from clearance_desk.models import ClearanceReport, ProgressEvent, RunStatus
from clearance_desk.pipeline import ClearancePipeline
from clearance_desk.report import render_markdown
from clearance_desk.research import FixtureResearcher


class StubDrafter:
    def draft_outreach(self, item, finding, verdict, project):  # noqa: ANN001
        return template_draft(item, finding, verdict, project)

    def summarise(self, report):  # noqa: ANN001
        return deterministic_summary(report)


@pytest.fixture
def recorded(tmp_path: Path) -> tuple[ClearanceReport, list[ProgressEvent], Path]:
    events: list[ProgressEvent] = []
    pipeline = ClearancePipeline(
        settings=Settings(use_fixtures=True),
        provider=FixtureResearcher(),
        drafter=StubDrafter(),
    )
    report = pipeline.run(demo_project(), items=demo_items(), on_event=events.append)
    path = tmp_path / "demo_run.json"
    save_demo_run(report, events, path)
    return report, events, path


def test_recording_round_trips(recorded) -> None:  # noqa: ANN001
    report, events, path = recorded
    loaded = load_demo_run(path)
    assert loaded is not None
    restored_report, restored_events = loaded
    assert restored_report.run_id == report.run_id
    assert len(restored_report.entries) == len(report.entries)
    assert restored_report.tier_counts == report.tier_counts
    assert len(restored_events) == len(events)


def test_recording_preserves_citations(recorded) -> None:  # noqa: ANN001
    report, _, path = recorded
    restored, _ = load_demo_run(path)
    assert restored.total_citations == report.total_citations
    assert restored.total_citations > 0


def test_missing_recording_is_not_an_error(tmp_path: Path) -> None:
    assert load_demo_run(tmp_path / "absent.json") is None
    assert has_recording(tmp_path / "absent.json") is False


def test_corrupt_recording_degrades_to_none(tmp_path: Path) -> None:
    """A damaged cache must fall back to a live run, not crash the service."""
    path = tmp_path / "demo_run.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert load_demo_run(path) is None


# ---------------------------------------------------------------------------
# Honesty of the replay
# ---------------------------------------------------------------------------


def test_replay_is_labelled_as_one(recorded) -> None:  # noqa: ANN001
    report, events, _ = recorded
    replay, _ = as_replay(report, events, "run_new")
    assert replay.is_replay
    assert replay.replay_of == report.run_id
    assert replay.replay_recorded_at is not None


def test_a_live_report_is_not_labelled_a_replay(recorded) -> None:  # noqa: ANN001
    report, _, _ = recorded
    assert report.is_replay is False
    assert "Replay" not in render_markdown(report)


def test_rendered_replay_discloses_its_provenance(recorded) -> None:  # noqa: ANN001
    report, events, _ = recorded
    replay, _ = as_replay(report, events, "run_new")
    markdown = render_markdown(replay)
    assert "Replay" in markdown
    assert report.run_id in markdown


def test_replay_carries_the_new_identifier_throughout(recorded) -> None:  # noqa: ANN001
    report, events, _ = recorded
    replay, replay_events = as_replay(report, events, "run_new")
    assert replay.run_id == "run_new"
    assert all(e.run_id == "run_new" for e in replay_events)


def test_replay_does_not_mutate_the_recording(recorded) -> None:  # noqa: ANN001
    """The stored run must survive being served many times."""
    report, events, _ = recorded
    original_id = report.run_id
    as_replay(report, events, "run_a")
    as_replay(report, events, "run_b")
    assert report.run_id == original_id
    assert report.is_replay is False
    assert all(e.run_id == original_id for e in events)


def test_replayed_findings_are_unchanged(recorded) -> None:  # noqa: ANN001
    report, events, _ = recorded
    replay, _ = as_replay(report, events, "run_new")
    assert replay.status is RunStatus.COMPLETE
    assert replay.tier_counts == report.tier_counts
    assert replay.total_citations == report.total_citations
    assert len(replay.blocking_entries) == len(report.blocking_entries)
