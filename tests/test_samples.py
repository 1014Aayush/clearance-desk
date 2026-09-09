"""Tests for the bundled sample cuts.

Two properties matter here and they pull in different directions.

The commercial one: a sample that has been recorded must replay, never
research. Five samples on a public URL that each researched live would bill the
account hosting the page once per tester per clip — worse than the single demo
button this project already refused to ship.

The honest one: a replay must still announce itself as a replay, and a sample
that has *not* been recorded must not quietly research instead. Spending is
explicit — the endpoint refuses rather than billing a caller who did not ask.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from clearance_desk.demo_cache import as_replay
from clearance_desk.models import (
    ClearanceReport,
    ProgressEvent,
    ProjectMeta,
    RunStatus,
)
from clearance_desk.samples import (
    Sample,
    get_sample,
    load_sample_recording,
    load_samples,
    samples_dir,
    save_sample_recording,
)

MANIFEST = {
    "samples": [
        {
            "id": "reel-one",
            "title": "Reel One",
            "year": 1956,
            "producer": "Somebody",
            "sponsor": "A Sponsor",
            "video": "reel-one.mp4",
            "poster": "reel-one.jpg",
            "cue_sheet": "reel-one.cue.csv",
            "recording": "reel-one.run.json",
            "excerpt": "0:00-1:00",
            "runtime_seconds": 60,
            "source_url": "https://example.invalid/reel-one",
            "rights": "Public domain",
            "blurb": "A blurb.",
            "exercises": "Brands and music.",
        }
    ]
}


def _write_manifest(directory: Path, payload: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture
def sample_dir(tmp_path: Path) -> Path:
    _write_manifest(tmp_path, MANIFEST)
    (tmp_path / "reel-one.mp4").write_bytes(b"\x00" * 32)
    return tmp_path


def test_loads_a_sample_from_the_manifest(sample_dir: Path) -> None:
    samples = load_samples(sample_dir)
    assert [s.id for s in samples] == ["reel-one"]
    sample = samples[0]
    assert sample.title == "Reel One"
    assert sample.video_path == sample_dir / "reel-one.mp4"
    assert sample.has_video()


def test_a_sample_with_no_video_is_not_offered(tmp_path: Path) -> None:
    """A listed-but-missing clip would surface to the tester as a dead button."""
    _write_manifest(tmp_path, MANIFEST)
    assert load_samples(tmp_path) == []


def test_a_malformed_entry_does_not_sink_the_rest(tmp_path: Path) -> None:
    payload = {"samples": [{"title": "no id, no video"}, *MANIFEST["samples"]]}
    _write_manifest(tmp_path, payload)
    (tmp_path / "reel-one.mp4").write_bytes(b"\x00")
    assert [s.id for s in load_samples(tmp_path)] == ["reel-one"]


def test_a_missing_manifest_is_not_an_error(tmp_path: Path) -> None:
    """An install without the assets directory simply offers no samples."""
    assert load_samples(tmp_path) == []


def test_unreadable_manifest_is_not_an_error(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text("{not json", encoding="utf-8")
    assert load_samples(tmp_path) == []


def test_get_sample_finds_by_id_and_returns_none_otherwise(sample_dir: Path) -> None:
    assert get_sample("reel-one", sample_dir) is not None
    assert get_sample("nope", sample_dir) is None


def test_the_browser_payload_never_carries_a_filesystem_path(
    sample_dir: Path,
) -> None:
    """Same rule as uploads: the client gets an id, not a path."""
    payload = load_samples(sample_dir)[0].as_dict()
    flat = json.dumps(payload)
    assert str(sample_dir) not in flat
    assert "reel-one.mp4" not in flat
    assert payload["id"] == "reel-one"
    assert payload["has_recording"] is False


def test_samples_dir_is_overridable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SAMPLES_DIR", "/somewhere/else")
    assert samples_dir() == Path("/somewhere/else")


def _report() -> ClearanceReport:
    return ClearanceReport(
        run_id="run_sample",
        project=ProjectMeta(title="Reel One"),
        status=RunStatus.COMPLETE,
    )


def test_recording_round_trips(sample_dir: Path) -> None:
    sample = load_samples(sample_dir)[0]
    assert load_sample_recording(sample) is None
    assert not sample.has_recording()

    events = [
        ProgressEvent(
            run_id="run_sample", stage=RunStatus.RESEARCHING, message="looked it up"
        )
    ]
    path = save_sample_recording(sample, _report(), events)
    assert path == sample_dir / "reel-one.run.json"

    reloaded = load_samples(sample_dir)[0]
    assert reloaded.has_recording()
    loaded = load_sample_recording(reloaded)
    assert loaded is not None
    report, loaded_events = loaded
    assert report.run_id == "run_sample"
    assert [e.message for e in loaded_events] == ["looked it up"]


def test_a_replayed_sample_is_labelled_as_a_replay(sample_dir: Path) -> None:
    """The recording is a real pass, but serving it again must say so."""
    sample = load_samples(sample_dir)[0]
    save_sample_recording(sample, _report(), [])
    recorded = load_sample_recording(load_samples(sample_dir)[0])
    assert recorded is not None
    report, events = recorded

    replay, _ = as_replay(report, events, "run_new")
    assert replay.run_id == "run_new"
    assert replay.replay_of == "run_sample"
    assert replay.replay_recorded_at is not None


def test_a_sample_without_a_recording_file_cannot_be_saved(sample_dir: Path) -> None:
    bare = Sample(
        id="x",
        title="X",
        year=None,
        producer=None,
        sponsor=None,
        blurb="",
        exercises="",
        excerpt=None,
        runtime_seconds=None,
        source_url="",
        rights="",
        video="x.mp4",
        poster=None,
        cue_sheet=None,
        recording=None,
        directory=sample_dir,
    )
    with pytest.raises(ValueError):
        save_sample_recording(bare, _report(), [])


# ---------------------------------------------------------------------------
# The HTTP surface
# ---------------------------------------------------------------------------


@pytest.fixture
def client(sample_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("SAMPLES_DIR", str(sample_dir))
    (sample_dir / "reel-one.jpg").write_bytes(b"\xff\xd8\xff")

    from clearance_desk import server

    return TestClient(server.app)


def test_api_lists_the_samples(client) -> None:  # noqa: ANN001
    body = client.get("/api/samples").json()
    assert [s["id"] for s in body["samples"]] == ["reel-one"]
    assert body["samples"][0]["has_poster"] is True


def test_api_serves_the_clip_and_its_poster(client) -> None:  # noqa: ANN001
    assert client.get("/api/samples/reel-one/video").status_code == 200
    assert client.get("/api/samples/reel-one/poster").status_code == 200


def test_api_404s_an_unknown_sample(client) -> None:  # noqa: ANN001
    assert client.get("/api/samples/nope/video").status_code == 404
    assert client.get("/api/samples/nope/poster").status_code == 404
    assert client.post("/api/runs/sample/nope").status_code == 404


def test_an_unrecorded_sample_refuses_rather_than_billing(client) -> None:  # noqa: ANN001
    """A plain POST must never spend money.

    Without a recording the honest options are to record one or to ask for a
    live pass explicitly; quietly researching would bill a caller who never
    asked to be billed.
    """
    res = client.post("/api/runs/sample/reel-one")
    assert res.status_code == 409
    assert "record-sample" in res.json()["detail"]
    assert "live=true" in res.json()["detail"]


def test_a_recorded_sample_replays_rather_than_researching(
    client, sample_dir: Path
) -> None:  # noqa: ANN001
    """The whole point: a tester costs nothing once the pass is recorded."""
    sample = load_samples(sample_dir)[0]
    save_sample_recording(sample, _report(), [])

    body = client.post("/api/runs/sample/reel-one").json()
    assert body["status"] == RunStatus.COMPLETE.value

    report = client.get(f"/api/runs/{body['run_id']}").json()["report"]
    assert report["replay_of"] == "run_sample"
    assert report["replay_recorded_at"]


# ---------------------------------------------------------------------------
# The public deployment's spending guard
# ---------------------------------------------------------------------------


def _budget():
    from clearance_desk.server import _LiveRunBudget

    return _LiveRunBudget()


def test_the_budget_stops_at_the_limit() -> None:
    """A public, unauthenticated URL spending a fixed prepaid balance needs the
    limit enforced on the server. The browser's confirmation dialog is not a
    control — a loop against the API never sees it."""
    budget = _budget()
    assert [budget.take(3) for _ in range(5)] == [True, True, True, False, False]
    assert budget.spent_today() == 3


def test_a_zero_limit_means_no_cap() -> None:
    """Running from source should not inherit a limit written for a public URL."""
    budget = _budget()
    assert all(budget.take(0) for _ in range(50))
    assert budget.spent_today() == 0


def test_the_budget_resets_the_next_day() -> None:
    import datetime as _dt

    budget = _budget()
    assert budget.take(1) is True
    assert budget.take(1) is False
    budget._day = _dt.date(2000, 1, 1)  # yesterday, as far as the counter knows
    assert budget.take(1) is True


def test_replays_are_never_charged_against_the_budget(client) -> None:  # noqa: ANN001
    """The samples are the point of the page. A visitor clicking through all
    eight must never be told the tool is out of budget."""
    from clearance_desk import server

    server._live_budget = server._LiveRunBudget()

    # A sample with no recording refuses rather than billing. That refusal
    # must not cost budget either — nothing was spent.
    assert client.post("/api/runs/sample/reel-one").status_code == 409
    assert server._live_budget.spent_today() == 0

    # Once it has a recorded pass, replaying it is free however often.
    save_sample_recording(get_sample("reel-one"), _report(), [])
    for _ in range(8):
        res = client.post("/api/runs/sample/reel-one")
        assert res.status_code == 202, res.text
    assert server._live_budget.spent_today() == 0
