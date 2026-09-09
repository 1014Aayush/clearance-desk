"""Tests for acoustic identification.

The point of this stage is not to name music for its own sake — it is to hand
the research stage something it can look up. So the test that matters most is
that a match actually propagates into ``known_work``, and that a miss degrades
to the honest "unidentified" path rather than inventing a title.
"""

from __future__ import annotations

import pytest

from clearance_desk.audio_id import (
    MIN_SAMPLE_SECONDS,
    SAMPLE_SECONDS,
    MusicMatch,
    NullIdentifier,
    identify_music,
    needs_identification,
    sample_window,
)
from clearance_desk.config import Settings
from clearance_desk.models import Category, Identifiability, Prominence, RiskItem


def cue(**overrides) -> RiskItem:
    base = dict(
        id="itm_cue",
        category=Category.MUSIC_SYNC,
        title="Unnamed track on car radio",
        description="Plays under the scene",
        start_seconds=862,
        end_seconds=931,
        prominence=Prominence.FEATURED,
        identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
    )
    base.update(overrides)
    return RiskItem(**base)


class StubIdentifier:
    name = "stub"

    def __init__(self, match: MusicMatch | None) -> None:
        self.match = match
        self.calls = 0

    def identify(self, audio: bytes) -> MusicMatch | None:  # noqa: ARG002
        self.calls += 1
        return self.match


# ---------------------------------------------------------------------------
# Which cues are worth a lookup
# ---------------------------------------------------------------------------


def test_unnamed_music_with_a_timecode_is_a_candidate() -> None:
    assert needs_identification(cue())


def test_already_named_music_is_left_alone() -> None:
    """No point paying to identify something the spotter recognised."""
    assert not needs_identification(cue(known_work="Rhapsody in Blue"))


def test_music_without_a_timecode_cannot_be_sampled() -> None:
    assert not needs_identification(cue(start_seconds=None))


@pytest.mark.parametrize(
    "category", [Category.TRADEMARK, Category.ARTWORK, Category.PERSON_LIKENESS]
)
def test_non_music_is_never_fingerprinted(category: Category) -> None:
    assert not needs_identification(cue(category=category))


# ---------------------------------------------------------------------------
# Where the sample is cut from
#
# Found in testing: a fixed twenty-second window over a short cue drags in
# whatever follows and dilutes the fingerprint until it stops matching.
# ---------------------------------------------------------------------------


def test_a_long_cue_skips_the_lead_in() -> None:
    """Openings are often a fade or buried under dialogue."""
    offset, length = sample_window(cue(start_seconds=20, end_seconds=55))
    assert offset == 22
    assert length == 20


def test_a_short_cue_is_never_over_run() -> None:
    offset, length = sample_window(cue(start_seconds=20, end_seconds=26))
    assert offset == 20  # no lead-in to spare
    assert offset + length <= 26


def test_a_very_short_sting_still_gets_a_window() -> None:
    offset, length = sample_window(cue(start_seconds=10, end_seconds=12))
    assert offset == 10
    assert length >= MIN_SAMPLE_SECONDS


def test_unknown_duration_falls_back_to_the_full_window() -> None:
    offset, length = sample_window(cue(start_seconds=30, end_seconds=None))
    assert offset == 30
    assert length == SAMPLE_SECONDS


@pytest.mark.parametrize("duration", [4, 8, 9, 20, 60, 600])
def test_sample_never_extends_past_the_cue(duration: int) -> None:
    offset, length = sample_window(cue(start_seconds=100, end_seconds=100 + duration))
    assert offset >= 100
    assert offset + length <= 100 + duration + MIN_SAMPLE_SECONDS


# ---------------------------------------------------------------------------
# The match, and what it feeds forward
# ---------------------------------------------------------------------------


def test_year_is_parsed_from_the_release_date() -> None:
    assert MusicMatch(title="X", release_date="1971-05-01").year == 1971
    assert MusicMatch(title="X", release_date="").year is None
    assert MusicMatch(title="X", release_date="unknown").year is None


def test_work_title_includes_the_artist() -> None:
    assert (
        MusicMatch(title="Ain't No Sunshine", artist="Bill Withers").as_work_title()
        == "Ain't No Sunshine — Bill Withers"
    )
    assert MusicMatch(title="Solo").as_work_title() == "Solo"


def test_a_match_becomes_a_researchable_work(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole reason this stage exists."""
    monkeypatch.setattr("clearance_desk.audio_id.ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "clearance_desk.audio_id.extract_audio_segment",
        lambda *a, **k: b"fake-audio",
    )
    item = cue()
    identifier = StubIdentifier(
        MusicMatch(title="Ain't No Sunshine", artist="Bill Withers", release_date="1971-05-01")
    )

    matches = identify_music([item], "cut.mp4", identifier, Settings())

    assert item.id in matches
    assert item.known_work == "Ain't No Sunshine — Bill Withers"
    assert item.known_year == 1971
    # And the item is now something research can act on.
    assert "Ain't No Sunshine" in item.research_subject()


def test_identification_is_recorded_as_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The spotter's account of a cue is an impression; the fingerprint is
    evidence. A reader has to be able to tell which is which — especially when
    they disagree, which on a real run they did."""
    monkeypatch.setattr("clearance_desk.audio_id.ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "clearance_desk.audio_id.extract_audio_segment", lambda *a, **k: b"audio"
    )
    item = cue(evidence="Audible on the soundtrack.")
    identify_music(
        [item],
        "cut.mp4",
        StubIdentifier(
            MusicMatch(title="Everybody Wants To Rule The World",
                       artist="Tears For Fears", label="Mercury")
        ),
        Settings(),
    )
    assert "Audible on the soundtrack" in item.evidence
    assert "Identified acoustically" in item.evidence
    assert "Mercury" in item.evidence


def test_a_miss_leaves_the_cue_unnamed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Original score is in no catalogue — it must not be given a title."""
    monkeypatch.setattr("clearance_desk.audio_id.ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "clearance_desk.audio_id.extract_audio_segment", lambda *a, **k: b"audio"
    )
    item = cue()
    assert identify_music([item], "cut.mp4", StubIdentifier(None), Settings()) == {}
    assert item.known_work is None


def test_missing_ffmpeg_degrades_quietly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clearance_desk.audio_id.ffmpeg_available", lambda: False)
    item = cue()
    identifier = StubIdentifier(MusicMatch(title="Anything"))
    assert identify_music([item], "cut.mp4", identifier, Settings()) == {}
    assert identifier.calls == 0
    assert item.known_work is None


def test_unreadable_audio_does_not_call_the_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("clearance_desk.audio_id.ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "clearance_desk.audio_id.extract_audio_segment", lambda *a, **k: None
    )
    identifier = StubIdentifier(MusicMatch(title="Anything"))
    assert identify_music([cue()], "cut.mp4", identifier, Settings()) == {}
    assert identifier.calls == 0


def test_no_source_means_no_lookup() -> None:
    identifier = StubIdentifier(MusicMatch(title="Anything"))
    assert identify_music([cue()], None, identifier, Settings()) == {}
    assert identifier.calls == 0


def test_null_identifier_is_a_no_op() -> None:
    assert identify_music([cue()], "cut.mp4", NullIdentifier(), Settings()) == {}


def test_named_cues_are_not_re_identified(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clearance_desk.audio_id.ffmpeg_available", lambda: True)
    monkeypatch.setattr(
        "clearance_desk.audio_id.extract_audio_segment", lambda *a, **k: b"audio"
    )
    identifier = StubIdentifier(MusicMatch(title="Something Else"))
    named = cue(known_work="Rhapsody in Blue", known_year=1924)
    identify_music([named], "cut.mp4", identifier, Settings())
    assert identifier.calls == 0
    assert named.known_work == "Rhapsody in Blue"
