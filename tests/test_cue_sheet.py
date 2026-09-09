"""Tests for reading and applying a music cue sheet.

Cue sheets have no standard format — every studio, library and PRO exports a
different shape — so the parser has to be forgiving about column names and
timecode styles. And because the cue sheet is the production's own signed
declaration, applying it has to *win* over what was inferred from the audio.
"""

from __future__ import annotations

import pytest

from clearance_desk.cue_sheet import (
    CueEntry,
    apply_cue_sheet,
    parse_cue_sheet,
    parse_cue_timecode,
)
from clearance_desk.models import (
    Category,
    Identifiability,
    Prominence,
    RiskItem,
    Source,
)

STANDARD = """Cue No,Cue Title,Composer,Publisher,Time In,Time Out,Use
1,Main Title,J. Okafor,Blue Room Music,0:00:12,0:01:44,Background Instrumental
2,Everybody Wants To Rule The World,Orzabal/Smith,Universal,0:14:22,0:15:31,Visual Vocal
3,Slow Tension Bed 04,,Audio Network,0:48:10,0:50:50,Library
"""


def music(**overrides) -> RiskItem:
    base = dict(
        id="itm_m",
        category=Category.MUSIC_SYNC,
        title="Unnamed cue",
        description="Music under the scene",
        start_seconds=862,
        end_seconds=931,
        prominence=Prominence.BACKGROUND,
        identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
    )
    base.update(overrides)
    return RiskItem(**base)


# ---------------------------------------------------------------------------
# Timecodes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,seconds",
    [
        ("0:14:22", 862),
        ("00:14:22", 862),
        ("14:22", 862),
        ("1:02:03", 3723),
        ("00:14:22:12", 862),  # frames dropped, not converted
        ("", None),
        ("not a timecode", None),
    ],
)
def test_cue_timecodes(text: str, seconds: int | None) -> None:
    assert parse_cue_timecode(text) == seconds


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parses_a_standard_sheet() -> None:
    sheet = parse_cue_sheet(STANDARD)
    assert len(sheet.entries) == 3
    assert sheet.entries[1].title == "Everybody Wants To Rule The World"
    assert sheet.entries[1].publisher == "Universal"
    assert sheet.entries[1].start_seconds == 862


def test_library_use_is_detected() -> None:
    sheet = parse_cue_sheet(STANDARD)
    assert sheet.entries[2].is_library
    assert not sheet.entries[0].is_library


@pytest.mark.parametrize(
    "header",
    [
        "Song Title,Writers,Publishers,Start,End,Usage",
        "Composition,Composer,Administrator,TC In,TC Out,Cue Type",
        "TITLE,COMPOSER,PUBLISHER,IN,OUT,USE",
    ],
)
def test_column_aliases_are_tolerated(header: str) -> None:
    """Every exporter names its columns differently."""
    text = header + "\nMain Title,J. Okafor,Blue Room,0:00:12,0:01:44,Background\n"
    sheet = parse_cue_sheet(text)
    assert len(sheet.entries) == 1
    assert sheet.entries[0].title == "Main Title"
    assert sheet.entries[0].start_seconds == 12


def test_tab_separated_is_handled() -> None:
    text = "Title\tPublisher\tIn\tOut\nMain Title\tBlue Room\t0:00:12\t0:01:44\n"
    assert len(parse_cue_sheet(text).entries) == 1


def test_duration_fills_in_a_missing_end() -> None:
    text = "Title,In,Duration\nMain Title,0:01:00,0:00:30\n"
    assert parse_cue_sheet(text).entries[0].end_seconds == 90


def test_missing_title_column_is_reported_not_guessed() -> None:
    sheet = parse_cue_sheet("Foo,Bar\n1,2\n")
    assert sheet.entries == []
    assert any("title column" in w for w in sheet.warnings)


def test_blank_rows_are_skipped_and_counted() -> None:
    sheet = parse_cue_sheet(STANDARD + ",,,,,,\n,,,,,,\n")
    assert len(sheet.entries) == 3
    assert sheet.skipped_rows == 2


def test_empty_file_warns() -> None:
    assert parse_cue_sheet("").warnings


def test_bytes_with_a_bom_are_read() -> None:
    """Excel exports UTF-8 with a byte order mark."""
    sheet = parse_cue_sheet(("﻿" + STANDARD).encode("utf-8"))
    assert len(sheet.entries) == 3


# ---------------------------------------------------------------------------
# Applying it — the cue sheet outranks the audio
# ---------------------------------------------------------------------------


def test_an_unnamed_cue_gets_its_real_title() -> None:
    item = music(start_seconds=862)
    result = apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.known_work == "Everybody Wants To Rule The World"
    assert result.named == 1
    assert item.source is Source.CUE_SHEET


def test_the_publisher_of_record_is_recorded() -> None:
    item = music(start_seconds=862)
    apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert "Universal" in (item.evidence or "")


def test_only_the_cue_sheet_may_declare_library_music() -> None:
    """The claim the picture pass is forbidden from making."""
    item = music(start_seconds=2890, end_seconds=3050, category=Category.MUSIC_SYNC)
    result = apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.category is Category.MUSIC_LIBRARY
    assert result.marked_library == 1


def test_adjacent_cues_do_not_swallow_each_other() -> None:
    """Seen on a real run: a library bed running to 0:19 sat inside the drift
    tolerance of a song starting at 0:20, and was dropped from the report."""
    text = (
        "Title,Publisher,In,Out,Use\n"
        "Opening Bed,Audio Network,0:00:00,0:00:19,Library\n"
        "Everybody Wants To Rule The World,Universal,0:00:20,0:00:54,Visual Vocal\n"
    )
    items = [music(start_seconds=20, end_seconds=54)]
    apply_cue_sheet(items, parse_cue_sheet(text))

    titles = {i.known_work for i in items}
    assert "Everybody Wants To Rule The World" in titles
    assert "Opening Bed" in titles, "the adjacent library cue was dropped"
    assert len(items) == 2


def test_the_nearer_cue_wins_when_windows_overlap() -> None:
    """Drift tolerance makes back-to-back cues overlap; containment decides."""
    text = (
        "Title,In,Out,Use\n"
        "Opening Bed,0:00:00,0:00:19,Library\n"
        "The Song,0:00:20,0:00:54,Visual Vocal\n"
    )
    item = music(start_seconds=20, end_seconds=54)
    apply_cue_sheet([item], parse_cue_sheet(text))
    assert item.known_work == "The Song"


def test_the_nearer_cue_wins_regardless_of_row_order() -> None:
    text = (
        "Title,In,Out,Use\n"
        "The Song,0:00:20,0:00:54,Visual Vocal\n"
        "Opening Bed,0:00:00,0:00:19,Library\n"
    )
    item = music(start_seconds=20, end_seconds=54)
    apply_cue_sheet([item], parse_cue_sheet(text))
    assert item.known_work == "The Song"


def test_usage_code_raises_prominence_on_a_matched_cue() -> None:
    """'Visual vocal' means heard in the clear — the production knows, the
    listener guesses."""
    item = music(start_seconds=862, prominence=Prominence.BACKGROUND)
    apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.prominence is Prominence.FEATURED
    assert item.audible_in_clear is True


def test_usage_code_never_lowers_what_was_observed() -> None:
    item = music(start_seconds=2890, end_seconds=3050, prominence=Prominence.HERO)
    apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.prominence is Prominence.HERO


def test_a_cue_the_picture_pass_missed_is_added() -> None:
    """Music under dialogue is easy to miss and still has to be cleared."""
    items = [music(start_seconds=862)]
    result = apply_cue_sheet(items, parse_cue_sheet(STANDARD))
    assert result.added == 2
    titles = {i.known_work for i in items}
    assert "Main Title" in titles
    assert "Slow Tension Bed 04" in titles


def test_matching_tolerates_a_small_timecode_drift() -> None:
    """Spotted timecodes and cue sheet timecodes never agree exactly."""
    item = music(start_seconds=866)  # four seconds late
    apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.known_work == "Everybody Wants To Rule The World"


def test_a_distant_cue_does_not_match() -> None:
    item = music(start_seconds=4000)
    result = apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.known_work is None
    assert result.named == 0


def test_an_already_named_cue_is_not_overwritten() -> None:
    item = music(start_seconds=862, known_work="Something The Spotter Knew")
    apply_cue_sheet([item], parse_cue_sheet(STANDARD))
    assert item.known_work == "Something The Spotter Knew"


def test_non_music_items_are_untouched() -> None:
    brand = RiskItem(
        id="itm_b",
        category=Category.TRADEMARK,
        title="Coca-Cola",
        description="Can on the counter",
        start_seconds=862,
    )
    apply_cue_sheet([brand], parse_cue_sheet(STANDARD))
    assert brand.category is Category.TRADEMARK
    assert brand.known_work is None


def test_featured_usage_sets_prominence() -> None:
    items: list[RiskItem] = []
    apply_cue_sheet(items, parse_cue_sheet(STANDARD))
    vocal = next(i for i in items if i.known_work.startswith("Everybody"))
    assert vocal.prominence is Prominence.FEATURED
    assert vocal.audible_in_clear is True


def test_unplaced_cues_are_still_added() -> None:
    sheet = parse_cue_sheet("Title,Publisher\nUntimed Cue,Some Publisher\n")
    items: list[RiskItem] = []
    result = apply_cue_sheet(items, sheet)
    assert result.added == 1
    assert items[0].start_seconds is None
    assert any("no timecode" in w for w in sheet.warnings)


def test_covers_respects_the_window() -> None:
    entry = CueEntry(title="X", start_seconds=100, end_seconds=200)
    assert entry.covers(150)
    assert entry.covers(95)      # within tolerance
    assert not entry.covers(50)
    assert not entry.covers(400)
