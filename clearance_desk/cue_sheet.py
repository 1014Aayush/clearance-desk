"""Reading a music cue sheet.

A cue sheet is the list of every piece of music in a film — title, composer,
publisher, where it sits, and how it is used. The music editor builds one as
the cut comes together, distributors require it on delivery, and performance
societies pay composers from it. It is the production's own answer to the
question the picture pass cannot answer.

That makes it the highest authority this system has about music, and it is
treated that way. Where a cue sheet covers a timecode, it overrides what was
guessed from the audio:

* an unnamed cue gets its real title, composer and publisher;
* a cue marked as library music is pre-cleared, which is the one claim the
  picture pass is not permitted to make on its own (see
  ``_demote_unverifiable_library_music``);
* a cue the picture pass missed entirely is added.

Cue sheets have no universal format — every studio, library and PRO exports a
different shape — so parsing is deliberately forgiving about column names and
timecode styles, and reports what it could not read rather than guessing.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import uuid
from dataclasses import dataclass, field

from .models import Category, Confidence, Identifiability, Prominence, RiskItem, Source

logger = logging.getLogger(__name__)

# --- column aliases ---------------------------------------------------------

_COLUMNS: dict[str, tuple[str, ...]] = {
    "title": ("title", "cue title", "song title", "composition", "work", "track", "song"),
    "composer": ("composer", "composers", "writer", "writers", "written by"),
    "publisher": ("publisher", "publishers", "publishing", "administrator"),
    "performer": ("performer", "artist", "recording artist", "performed by"),
    "start": ("in", "start", "start time", "timecode in", "tc in", "cue in", "time in"),
    "end": ("out", "end", "end time", "timecode out", "tc out", "cue out", "time out"),
    "duration": ("duration", "length", "run time", "timing"),
    "usage": ("use", "usage", "use type", "type", "usage type", "cue type"),
    "library": ("library", "production library", "source"),
    "year": ("year", "release year", "copyright year"),
}

#: Cue sheet usage codes that mean "production library, cleared at source".
_LIBRARY_MARKERS = (
    "library",
    "production music",
    "prod music",
    "stock",
    "buyout",
    "royalty free",
    "royalty-free",
)

#: Usage codes that mean the music is heard in the clear rather than buried.
_FEATURED_MARKERS = ("visual vocal", "feature", "featured", "vocal", "main title", "end title")

_TIMECODE = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})(?::(\d{1,2}))?\s*$")


def parse_cue_timecode(value: str) -> int | None:
    """Parse ``MM:SS``, ``H:MM:SS`` or ``HH:MM:SS:FF`` into seconds.

    Cue sheets frequently carry a frames field. Frames are dropped rather than
    converted: nothing downstream is accurate to a frame, and pretending
    otherwise would be false precision.
    """
    if not value:
        return None
    match = _TIMECODE.match(str(value))
    if not match:
        return None
    hours, minutes, seconds, _frames = match.groups()
    if hours is None:
        # Two fields: ambiguous between MM:SS and HH:MM. Cue sheets that omit
        # hours are listing minutes and seconds.
        return int(minutes) * 60 + int(seconds)
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds)


@dataclass
class CueEntry:
    title: str
    composer: str | None = None
    publisher: str | None = None
    performer: str | None = None
    start_seconds: int | None = None
    end_seconds: int | None = None
    usage: str | None = None
    year: int | None = None
    is_library: bool = False

    @property
    def is_placed(self) -> bool:
        return self.start_seconds is not None

    def covers(self, seconds: int, tolerance: int = 10) -> bool:
        """Whether this cue sits over a given point in the cut."""
        if self.start_seconds is None:
            return False
        end = self.end_seconds if self.end_seconds is not None else self.start_seconds
        return (self.start_seconds - tolerance) <= seconds <= (end + tolerance)

    def work_title(self) -> str:
        return f"{self.title} — {self.performer}" if self.performer else self.title


@dataclass
class CueSheet:
    entries: list[CueEntry] = field(default_factory=list)
    skipped_rows: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def placed(self) -> list[CueEntry]:
        return [e for e in self.entries if e.is_placed]

    def covering(self, seconds: int) -> CueEntry | None:
        """The cue playing at a point in the cut.

        Matching allows a few seconds of drift, because a spotted timecode and
        a cue sheet timecode never agree exactly. That tolerance makes adjacent
        cues overlap — a bed ending at 0:19 and a song starting at 0:20 both
        reach second 20 — so a first-match scan would return whichever happened
        to be listed first. Containment is preferred over drift, and the
        nearest cue wins among the rest.
        """
        candidates = [e for e in self.placed if e.covers(seconds)]
        if not candidates:
            return None

        def rank(entry: CueEntry) -> tuple[int, int]:
            start = entry.start_seconds or 0
            end = entry.end_seconds if entry.end_seconds is not None else start
            contains = 0 if start <= seconds <= end else 1
            return (contains, min(abs(seconds - start), abs(seconds - end)))

        return min(candidates, key=rank)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _normalise_header(name: str) -> str:
    return re.sub(r"[^a-z ]+", " ", (name or "").lower()).strip()


def _map_columns(fieldnames: list[str]) -> dict[str, str]:
    """Map a cue sheet's own column names onto ours."""
    mapping: dict[str, str] = {}
    for raw in fieldnames or []:
        norm = _normalise_header(raw)
        if not norm:
            continue
        for key, aliases in _COLUMNS.items():
            if key in mapping:
                continue
            if norm in aliases or any(norm.startswith(a) for a in aliases):
                mapping[key] = raw
                break
    return mapping


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"y", "yes", "true", "1", "x", "library"}


def _as_year(value: str | None) -> int | None:
    match = re.search(r"(1[89]\d{2}|20\d{2})", value or "")
    return int(match.group(1)) if match else None


def parse_cue_sheet(data: str | bytes, filename: str = "cue_sheet.csv") -> CueSheet:
    """Parse a cue sheet from CSV or tab-separated text."""
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = data

    sheet = CueSheet()
    if not text.strip():
        sheet.warnings.append("The cue sheet is empty.")
        return sheet

    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    columns = _map_columns(reader.fieldnames or [])

    if "title" not in columns:
        sheet.warnings.append(
            "No title column found. Expected a header row containing one of: "
            + ", ".join(_COLUMNS["title"])
        )
        return sheet

    def cell(row: dict[str, str], key: str) -> str | None:
        column = columns.get(key)
        if column is None:
            return None
        value = (row.get(column) or "").strip()
        return value or None

    for row in reader:
        title = cell(row, "title")
        if not title:
            sheet.skipped_rows += 1
            continue

        usage = cell(row, "usage")
        library_cell = cell(row, "library")
        is_library = _truthy(library_cell) or any(
            marker in (usage or "").lower() for marker in _LIBRARY_MARKERS
        )
        if library_cell and not _truthy(library_cell):
            # A named library ("Audio Network") in the library column is itself
            # the declaration.
            is_library = True

        start = parse_cue_timecode(cell(row, "start") or "")
        end = parse_cue_timecode(cell(row, "end") or "")
        if start is not None and end is None:
            span = parse_cue_timecode(cell(row, "duration") or "")
            if span:
                end = start + span

        sheet.entries.append(
            CueEntry(
                title=title,
                composer=cell(row, "composer"),
                publisher=cell(row, "publisher"),
                performer=cell(row, "performer"),
                start_seconds=start,
                end_seconds=end,
                usage=usage,
                year=_as_year(cell(row, "year")),
                is_library=is_library,
            )
        )

    if not sheet.entries:
        sheet.warnings.append("No usable rows found in the cue sheet.")
    unplaced = len(sheet.entries) - len(sheet.placed)
    if unplaced:
        sheet.warnings.append(
            f"{unplaced} cue(s) carry no timecode and cannot be matched to the "
            "picture; they are added as separate items."
        )
    return sheet


# ---------------------------------------------------------------------------
# Applying it
# ---------------------------------------------------------------------------


@dataclass
class CueSheetResult:
    named: int = 0
    marked_library: int = 0
    added: int = 0
    unmatched_cues: list[str] = field(default_factory=list)

    @property
    def total_changes(self) -> int:
        return self.named + self.marked_library + self.added


def _entry_to_item(entry: CueEntry) -> RiskItem:
    return RiskItem(
        id=f"itm_{uuid.uuid4().hex[:10]}",
        category=Category.MUSIC_LIBRARY if entry.is_library else Category.MUSIC_SYNC,
        title=entry.work_title(),
        known_work=entry.work_title(),
        known_year=entry.year,
        description=(
            f"Listed on the cue sheet"
            + (f" as {entry.usage}" if entry.usage else "")
            + (f"; published by {entry.publisher}" if entry.publisher else "")
        ),
        source=Source.CUE_SHEET,
        start_seconds=entry.start_seconds,
        end_seconds=entry.end_seconds,
        prominence=(
            Prominence.FEATURED
            if any(m in (entry.usage or "").lower() for m in _FEATURED_MARKERS)
            else Prominence.BACKGROUND
        ),
        identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        audible_in_clear=any(
            m in (entry.usage or "").lower() for m in _FEATURED_MARKERS
        ),
        detection_confidence=Confidence.HIGH,
        evidence=(
            "Declared on the production's cue sheet"
            + (f" (composer: {entry.composer})" if entry.composer else "")
        ),
    )


def apply_cue_sheet(items: list[RiskItem], sheet: CueSheet) -> CueSheetResult:
    """Reconcile spotted music against the production's own cue sheet.

    The cue sheet wins. It is a document the production signed its name to,
    where the picture pass is an inference from audio.
    """
    result = CueSheetResult()
    matched_entries: set[int] = set()

    music = [i for i in items if i.category in (Category.MUSIC_SYNC, Category.MUSIC_LIBRARY)]

    for item in music:
        if item.start_seconds is None:
            continue
        entry = sheet.covering(item.start_seconds)
        if entry is None:
            continue
        matched_entries.add(id(entry))

        if not item.known_work:
            item.known_work = entry.work_title()
            item.known_year = entry.year
            result.named += 1

        if entry.is_library and item.category is not Category.MUSIC_LIBRARY:
            # The one declaration only the production can make.
            item.category = Category.MUSIC_LIBRARY
            result.marked_library += 1

        # Usage codes are a statement about how the cue is heard, and the
        # production knows that better than a listener does. "Visual vocal"
        # means it plays in the clear, which raises the licence it needs; the
        # code never lowers what the picture pass observed.
        if any(m in (entry.usage or "").lower() for m in _FEATURED_MARKERS):
            if item.prominence in (Prominence.INCIDENTAL, Prominence.BACKGROUND):
                item.prominence = Prominence.FEATURED
            item.audible_in_clear = True

        item.source = Source.CUE_SHEET
        item.detection_confidence = Confidence.HIGH
        note = "Confirmed against the production's cue sheet"
        if entry.publisher:
            note += f"; publisher of record {entry.publisher}"
        item.evidence = note

    # Cues the picture pass never noticed still have to be cleared.
    #
    # Membership of `matched_entries` is the only test. An earlier version also
    # skipped any entry whose window happened to reach a spotted item, which
    # collided on adjacent cues: a library bed running to 0:19 sits inside the
    # ±10s tolerance of a song starting at 0:20, so the bed was judged already
    # represented and silently dropped. Back-to-back cues are the normal shape
    # of a cue sheet, so that lost one nearly every time.
    for entry in sheet.entries:
        if id(entry) in matched_entries:
            continue
        items.append(_entry_to_item(entry))
        result.added += 1
        result.unmatched_cues.append(entry.work_title())

    return result
