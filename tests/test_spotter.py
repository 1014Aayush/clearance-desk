"""Tests for the pure logic of the spotting pass.

Everything here runs without touching Vertex AI. The windowing, timecode
offsetting and de-duplication are where a feature-length cut quietly goes
wrong, so they are pinned down independently of the model.
"""

from __future__ import annotations

import pytest

from clearance_desk.models import (
    Category,
    Confidence,
    Identifiability,
    Prominence,
    Source,
)
from clearance_desk.spotter import (
    ScriptSpotResponse,
    SpottedVideoItem,
    VideoSpotResponse,
    _parse_response,
    _safe_year,
    _to_risk_item,
    deduplicate,
    merge_sources,
    plan_windows,
)


def spotted(**overrides) -> SpottedVideoItem:
    base = dict(
        category=Category.TRADEMARK,
        title="Coca-Cola",
        description="Can on the table",
        start_timecode="00:14",
        end_timecode="00:19",
        prominence=Prominence.BACKGROUND,
        identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        audible_in_clear=False,
        depicted_negatively=False,
        no_published_identity=False,
        detection_confidence=Confidence.HIGH,
        evidence="Red can, logo facing camera",
        known_work="",
        known_year="",
    )
    base.update(overrides)
    return SpottedVideoItem(**base)


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


def test_short_cut_is_a_single_window() -> None:
    assert plan_windows(120) == plan_windows(120)
    assert len(plan_windows(120)) == 1


def test_feature_length_cut_is_windowed() -> None:
    windows = plan_windows(90 * 60)
    assert len(windows) > 15
    assert windows[0].start == 0
    assert windows[-1].end == 90 * 60


def test_windows_overlap_so_nothing_falls_through_a_seam() -> None:
    windows = plan_windows(1800)
    for previous, following in zip(windows, windows[1:]):
        assert following.start < previous.end


def test_windows_cover_the_whole_runtime() -> None:
    runtime = 3600
    windows = plan_windows(runtime)
    covered = 0
    for window in windows:
        assert window.start <= covered
        covered = max(covered, window.end)
    assert covered == runtime


def test_unknown_runtime_still_yields_a_window() -> None:
    assert len(plan_windows(None)) == 1


# ---------------------------------------------------------------------------
# Timecode offsetting — the bug that would silently misplace every item
# ---------------------------------------------------------------------------


def test_window_local_timecodes_are_shifted_onto_the_master_timeline() -> None:
    item = _to_risk_item(spotted(start_timecode="00:30", end_timecode="00:35"), 600)
    assert item is not None
    assert item.start_seconds == 630
    assert item.end_seconds == 635
    assert item.timecode == "0:10:30–0:10:35"


def test_reversed_timecodes_are_repaired() -> None:
    item = _to_risk_item(spotted(start_timecode="00:40", end_timecode="00:20"), 0)
    assert item is not None
    assert item.start_seconds == 20
    assert item.end_seconds == 40


def test_a_timecode_past_the_window_is_clamped() -> None:
    """Seen on the first real multi-window run: a 32-second window returned an
    item at 16:08 lasting eleven minutes, on a cut 5:22 long. Unclamped it
    sends a reviewer to a timecode that does not exist and quotes an
    eleven-minute appearance in a letter."""
    item = _to_risk_item(
        spotted(start_timecode="11:18", end_timecode="22:28"),
        offset_seconds=290,
        window_seconds=32,
    )
    assert item is not None
    assert item.start_seconds <= 290 + 32
    assert item.end_seconds <= 290 + 32


def test_a_clamped_item_says_so_and_loses_confidence() -> None:
    item = _to_risk_item(
        spotted(start_timecode="11:18", end_timecode="12:00", evidence="Seen in shot"),
        offset_seconds=0,
        window_seconds=30,
    )
    assert item is not None
    assert item.detection_confidence is Confidence.LOW
    assert "clamped" in (item.evidence or "")
    assert "Seen in shot" in (item.evidence or "")
    # The model's own reading is quoted, because "16:08" as sixteen minutes
    # versus sixteen seconds is the whole question and parsing destroys it.
    assert "11:18" in (item.evidence or "")
    assert "30s" in (item.evidence or "")


def test_timecodes_inside_the_window_are_untouched() -> None:
    item = _to_risk_item(
        spotted(start_timecode="00:10", end_timecode="00:20", evidence="Clear shot"),
        offset_seconds=300,
        window_seconds=300,
    )
    assert item is not None
    assert item.start_seconds == 310
    assert item.end_seconds == 320
    assert item.detection_confidence is Confidence.HIGH
    assert item.evidence == "Clear shot"


def test_a_few_seconds_of_overrun_is_tolerated() -> None:
    """Boundaries are approximate; only wild readings are treated as errors."""
    item = _to_risk_item(
        spotted(start_timecode="00:28", end_timecode="00:32"),
        offset_seconds=0,
        window_seconds=30,
    )
    assert item is not None
    assert item.detection_confidence is Confidence.HIGH
    assert item.end_seconds == 32


def test_unparseable_timecodes_drop_the_item_rather_than_guessing() -> None:
    assert _to_risk_item(spotted(start_timecode="sometime", end_timecode="later"), 0) is None


@pytest.mark.parametrize(
    "raw,expected",
    [("1971", 1971), ("c. 1889", 1889), ("released 2004", 2004), ("", None), ("unknown", None)],
)
def test_year_extraction(raw: str, expected: int | None) -> None:
    assert _safe_year(raw) == expected


def test_music_seen_in_picture_is_never_assumed_pre_cleared() -> None:
    """Observed on a real Pixel ad: the model called the score library music.

    Library cues are treated as pre-cleared. A commercial recording needs two
    licences and a month. Guessing the former is the dangerous direction, and
    the difference is not audible — only a cue sheet establishes it.
    """
    item = _to_risk_item(spotted(category=Category.MUSIC_LIBRARY, title="Score"), 0)
    assert item is not None
    assert item.category is Category.MUSIC_SYNC


def test_a_cue_sheet_may_still_declare_library_music() -> None:
    """The demotion applies to the picture pass, not to supplied paperwork."""
    from clearance_desk.models import RiskItem

    cue = RiskItem(
        id="itm_cue",
        category=Category.MUSIC_LIBRARY,
        title="Tension Bed 04",
        description="Listed on the composer's cue sheet",
        source=Source.CUE_SHEET,
    )
    assert cue.category is Category.MUSIC_LIBRARY


def test_empty_known_work_stays_empty() -> None:
    """A model that does not recognise a work must not be given a title."""
    item = _to_risk_item(spotted(known_work="", known_year=""), 0)
    assert item is not None
    assert item.known_work is None
    assert item.research_subject() == "Coca-Cola"


# ---------------------------------------------------------------------------
# De-duplication across window seams
# ---------------------------------------------------------------------------


def test_overlapping_windows_do_not_double_report() -> None:
    first = _to_risk_item(spotted(start_timecode="04:55", end_timecode="05:00"), 0)
    second = _to_risk_item(spotted(start_timecode="00:00", end_timecode="00:05"), 290)
    assert first and second
    assert len(deduplicate([first, second])) == 1


def test_the_same_brand_later_in_the_film_is_a_separate_item() -> None:
    first = _to_risk_item(spotted(start_timecode="00:14"), 0)
    second = _to_risk_item(spotted(start_timecode="00:14"), 3600)
    assert first and second
    assert len(deduplicate([first, second])) == 2


def test_merge_keeps_the_more_exposed_reading() -> None:
    """Under-calling prominence is the expensive mistake, so it loses."""
    quiet = _to_risk_item(
        spotted(prominence=Prominence.BACKGROUND, identifiability=Identifiability.PARTIAL),
        0,
    )
    loud = _to_risk_item(
        spotted(
            prominence=Prominence.HERO,
            identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
            start_timecode="00:16",
            end_timecode="00:30",
        ),
        0,
    )
    assert quiet and loud
    merged = deduplicate([quiet, loud])
    assert len(merged) == 1
    assert merged[0].prominence is Prominence.HERO
    assert merged[0].identifiability is Identifiability.CLEARLY_IDENTIFIABLE
    assert merged[0].end_seconds == 30


def test_merge_preserves_a_recognised_title() -> None:
    unnamed = _to_risk_item(spotted(category=Category.MUSIC_SYNC, title="radio song"), 0)
    named = _to_risk_item(
        spotted(
            category=Category.MUSIC_SYNC,
            title="radio song",
            known_work="Rhapsody in Blue",
            known_year="1924",
            start_timecode="00:16",
        ),
        0,
    )
    assert unnamed and named
    merged = deduplicate([unnamed, named])
    assert merged[0].known_work == "Rhapsody in Blue"
    assert merged[0].known_year == 1924


def test_negative_depiction_survives_a_merge() -> None:
    """A flag that changes the legal position must never be merged away."""
    neutral = _to_risk_item(spotted(), 0)
    negative = _to_risk_item(spotted(depicted_negatively=True, start_timecode="00:16"), 0)
    assert neutral and negative
    assert deduplicate([neutral, negative])[0].depicted_negatively is True


def test_different_categories_are_never_merged() -> None:
    brand = _to_risk_item(spotted(category=Category.TRADEMARK, title="Apple"), 0)
    artwork = _to_risk_item(spotted(category=Category.ARTWORK, title="Apple"), 0)
    assert brand and artwork
    assert len(deduplicate([brand, artwork])) == 2


def test_merge_sources_combines_picture_and_script() -> None:
    from clearance_desk.models import RiskItem

    video = _to_risk_item(spotted(), 0)
    script = RiskItem(
        id="itm_s",
        category=Category.SCRIPT_REFERENCE,
        title="Acme Corp",
        description="Named in dialogue",
        source=Source.SCRIPT,
        scene="INT. OFFICE - DAY",
    )
    assert video
    merged = merge_sources([video], [script])
    assert len(merged) == 2
    assert {i.source for i in merged} == {Source.VIDEO, Source.SCRIPT}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, parsed=None, text=None):  # noqa: ANN001
        self.parsed = parsed
        self.text = text


def test_parser_prefers_the_sdk_parsed_object() -> None:
    payload = VideoSpotResponse(items=[spotted()])
    assert _parse_response(_FakeResponse(parsed=payload), VideoSpotResponse) is payload


def test_parser_falls_back_to_raw_json() -> None:
    raw = VideoSpotResponse(items=[spotted()]).model_dump_json()
    parsed = _parse_response(_FakeResponse(text=raw), VideoSpotResponse)
    assert len(parsed.items) == 1


def test_unparseable_response_yields_no_items_rather_than_crashing() -> None:
    parsed = _parse_response(_FakeResponse(text="not json at all"), VideoSpotResponse)
    assert parsed.items == []


def test_empty_response_yields_no_items() -> None:
    assert _parse_response(_FakeResponse(), ScriptSpotResponse).items == []
