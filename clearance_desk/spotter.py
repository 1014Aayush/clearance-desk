"""The spotting pass: Gemini watches the cut and reads the script.

Spotting is the job a clearance supervisor does by hand — sit with the picture,
stop on every frame containing someone else's property, and write it down with
a timecode. On a feature that is days of work, and it is the step productions
skip when the delivery date closes in, which is exactly why items surface late.

Two design decisions matter here.

**The model observes; it does not adjudicate.** It is asked what is in the
frame, how prominently, and how legibly — never whether something is cleared,
risky or fine. Those conclusions belong to :mod:`clearance_desk.rules`, where
they are reproducible. Keeping the model on the descriptive side of that line
is what stops the report's legal position from drifting between runs.

**Long cuts are analysed in windows.** A feature does not fit comfortably in
one request, and sampling a 90-minute cut end-to-end at a low frame rate misses
exactly the brief background details that clearance cares about. The runtime is
split into overlapping windows analysed concurrently, and window-local
timecodes are shifted back onto the master timeline.
"""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .models import (
    Category,
    Confidence,
    Identifiability,
    Prominence,
    ProjectMeta,
    RiskItem,
    Source,
    parse_timecode,
)

logger = logging.getLogger(__name__)

#: Window length for long-form analysis. Long enough that the model sees scene
#: context, short enough that fine visual detail is not sampled away.
WINDOW_SECONDS = 300

#: Overlap between windows so an item straddling a boundary is not lost.
WINDOW_OVERLAP_SECONDS = 10

#: Frames per second sampled from the video. Clearance depends on brief
#: background detail, so this sits above the default sampling rate.
ANALYSIS_FPS = 2.0


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class SpottedVideoItem(BaseModel):
    """One observation from the picture pass."""

    category: Category
    title: str = Field(description="Short label: the brand, song, artwork or person")
    description: str = Field(description="What is actually visible or audible")
    start_timecode: str = Field(description="MM:SS or H:MM:SS, relative to this window")
    end_timecode: str = Field(description="MM:SS or H:MM:SS, relative to this window")
    prominence: Prominence
    identifiability: Identifiability
    audible_in_clear: bool = Field(
        description="Music only: audible without dialogue over it. False otherwise."
    )
    depicted_negatively: bool = Field(
        description="Shown unfavourably, or in a way implying endorsement"
    )
    no_published_identity: bool = Field(
        description=(
            "True for a one-off physical object or bespoke material with no "
            "public identity — a hand-painted mural, a custom neon sign, a "
            "built prop, footage shot for this production. False for anything "
            "mass-produced, branded, or published."
        )
    )
    detection_confidence: Confidence
    evidence: str = Field(description="What in the frame supports this observation")
    known_work: str = Field(
        description="Full title of the work if identifiable, else empty string"
    )
    known_year: str = Field(description="Year of the work if known, else empty string")


class SpottedScriptItem(BaseModel):
    """One observation from the script pass."""

    category: Category
    title: str
    description: str
    scene: str = Field(description="Scene heading or page reference")
    prominence: Prominence
    depicted_negatively: bool
    detection_confidence: Confidence
    evidence: str = Field(description="The line or action that triggered the flag")
    known_work: str
    known_year: str


class VideoSpotResponse(BaseModel):
    items: list[SpottedVideoItem]


class ScriptSpotResponse(BaseModel):
    items: list[SpottedScriptItem]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = """\
You are a clearance supervisor examining a cut of a film for material that \
belongs to somebody else.

Your job is to OBSERVE AND RECORD, never to advise. Do not say whether \
something is risky, cleared, fair use, de minimis or acceptable. Do not \
recommend action. Another system makes those determinations from your \
observations, and it can only do so if your observations are neutral.

Record every appearance of:

- Music. Any recorded song, score or source cue. Note whether it is audible in \
  the clear or buried under dialogue.
- Brands and trademarks. Logos, packaging, product livery, vehicle badges, \
  storefronts, uniforms.
- Artwork. Paintings, posters, murals, photographs, sculpture, album covers, \
  graffiti, tattoos, and any framed or mounted image.
- Archival or stock footage, and any film or television playing on a screen \
  within the scene.
- People who are recognisable on camera and are not obviously principal cast.
- Identifiable private property and distinctive architecture.
- Legible printed matter: newspapers, magazines, book covers, signage.
- Typefaces used in on-screen titles and graphics.

For each observation, judge three things carefully, because downstream \
decisions turn on them:

prominence
  incidental      fleeting, at the edge of frame, nobody would notice
  background      visible but plainly not the subject of the shot
  featured        deliberately framed, or audible in the clear
  hero            the camera rests on it; it is the subject of the shot
  plot_critical   the scene does not work without it

identifiability
  not_identifiable       nobody could recognise whose property this is
  partial                obscured, cropped, out of focus, or turned away
  clearly_identifiable   legible or recognisable enough to be claimed

detection_confidence
  How sure you are that you saw what you say you saw. Use low freely. A \
  hesitant observation is useful; a confident wrong one is not.

Rules:

- Timecodes must be MM:SS or H:MM:SS and must be relative to the START of the \
  clip you were given.
- Name the work in known_work only when you genuinely recognise it. An empty \
  string is the correct answer when you do not. Never guess a title, artist or \
  year — a fabricated title sends the research stage after the wrong rights \
  holder, which is worse than an unnamed item.
- Record each distinct appearance separately, even of the same brand or song.
- Classify all music as music_sync. You cannot hear the difference between a
  production library cue and a commercial recording, and neither can anyone
  else — that distinction is established by a cue sheet, not by listening.
- Set no_published_identity when the thing has no public identity at all: a
  mural painted on one particular wall, a neon sign made for one particular
  bar, a prop built for the shoot. These cannot be looked up by anybody, so
  saying so saves a pointless search. Leave it false for anything
  mass-produced, branded or published — a Coca-Cola can, a chart single, a
  gallery painting, a released film — even when you cannot name it precisely.
- Err towards recording. A false positive costs a reviewer ten seconds; a \
  missed item is discovered by a distributor's lawyer after picture lock.
"""

_SCRIPT_SYSTEM_INSTRUCTION = """\
You are a clearance supervisor reading a screenplay for material that will \
need clearing before delivery.

OBSERVE AND RECORD ONLY. Do not assess risk or recommend action.

Flag:

- Songs named in action lines or dialogue.
- Real companies, products and brands named in dialogue or action.
- Real, identifiable people named or depicted.
- Quoted lyrics, poetry, or passages from other written works.
- Film or television titles that characters watch or quote.
- Named real locations, businesses and institutions.
- Any reference to archival events implying archival footage.

Mark depicted_negatively as true when a real person, company or product is \
shown unfavourably, mocked, or connected to criminal or immoral conduct — this \
changes the legal position materially.

Give the scene heading in `scene`. Never invent a title, artist or year; leave \
known_work empty if you do not recognise the work.
"""


def _video_prompt(project: ProjectMeta, window_start: int, window_end: int) -> str:
    return (
        f"Production: '{project.title}' ({project.cut_label}).\n"
        f"Intended distribution: {project.distribution_intent}.\n\n"
        f"This clip covers {window_start // 60}:{window_start % 60:02d} to "
        f"{window_end // 60}:{window_end % 60:02d} of the full cut, but your "
        "timecodes must be relative to the start of THIS clip.\n\n"
        "Examine the picture and the audio. Record every piece of third-party "
        "material you observe."
    )


# ---------------------------------------------------------------------------
# Windowing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    start: int
    end: int

    @property
    def duration(self) -> int:
        return self.end - self.start


def plan_windows(
    runtime_seconds: int | None,
    window_seconds: int = WINDOW_SECONDS,
    overlap: int = WINDOW_OVERLAP_SECONDS,
) -> list[Window]:
    """Split a runtime into overlapping analysis windows."""
    if not runtime_seconds or runtime_seconds <= window_seconds:
        return [Window(0, runtime_seconds or 0)]

    windows: list[Window] = []
    start = 0
    while start < runtime_seconds:
        end = min(start + window_seconds, runtime_seconds)
        windows.append(Window(start, end))
        if end >= runtime_seconds:
            break
        start = end - overlap
    return windows


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def deduplicate(items: list[RiskItem], within_seconds: int = 15) -> list[RiskItem]:
    """Drop the double-reports produced by overlapping windows.

    Two observations of the same category and title within a few seconds of one
    another are the same appearance seen twice, not two appearances. The more
    exposed reading is kept, because under-calling prominence is the error that
    costs money.
    """
    kept: list[RiskItem] = []
    prominence_rank = {
        Prominence.INCIDENTAL: 0,
        Prominence.BACKGROUND: 1,
        Prominence.FEATURED: 2,
        Prominence.HERO: 3,
        Prominence.PLOT_CRITICAL: 4,
    }
    identifiability_rank = {
        Identifiability.NOT_IDENTIFIABLE: 0,
        Identifiability.PARTIAL: 1,
        Identifiability.CLEARLY_IDENTIFIABLE: 2,
    }

    for item in sorted(items, key=lambda i: (i.start_seconds or 0)):
        duplicate = None
        for existing in kept:
            if existing.category is not item.category:
                continue
            if _normalise(existing.title) != _normalise(item.title):
                continue
            if existing.source is not item.source:
                continue
            if item.source is not Source.VIDEO:
                duplicate = existing
                break
            gap = abs((item.start_seconds or 0) - (existing.start_seconds or 0))
            if gap <= within_seconds:
                duplicate = existing
                break

        if duplicate is None:
            kept.append(item)
            continue

        # Merge: keep the most exposed reading of the same appearance.
        if prominence_rank[item.prominence] > prominence_rank[duplicate.prominence]:
            duplicate.prominence = item.prominence
        if (
            identifiability_rank[item.identifiability]
            > identifiability_rank[duplicate.identifiability]
        ):
            duplicate.identifiability = item.identifiability
        duplicate.audible_in_clear = duplicate.audible_in_clear or item.audible_in_clear
        duplicate.depicted_negatively = (
            duplicate.depicted_negatively or item.depicted_negatively
        )
        if item.end_seconds and (
            duplicate.end_seconds is None or item.end_seconds > duplicate.end_seconds
        ):
            duplicate.end_seconds = item.end_seconds
        if not duplicate.known_work and item.known_work:
            duplicate.known_work = item.known_work
            duplicate.known_year = item.known_year

    return kept


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def _safe_year(raw: str) -> int | None:
    match = re.search(r"(1[89]\d{2}|20\d{2})", raw or "")
    return int(match.group(1)) if match else None


def _demote_unverifiable_library_music(category: Category) -> Category:
    """Music observed in picture can never be assumed pre-cleared.

    Nothing in a soundtrack distinguishes a production library cue from a
    commercial recording by ear — the difference is a paperwork fact, and only
    a cue sheet establishes it. Guessing ``music_library`` is the dangerous
    direction to be wrong in: it is treated as pre-cleared and closed with a
    cue sheet entry, where a commercial recording needs two separate licences
    and thirty days. So the picture pass is not permitted to make that claim;
    a cue sheet may still demote the item later.
    """
    return Category.MUSIC_SYNC if category is Category.MUSIC_LIBRARY else category


#: A model asked about a 32-second window has been seen returning timecodes
#: eleven minutes long. A few seconds of slop is ordinary; anything beyond this
#: is the model losing track of the clip it was given.
_TIMECODE_SLOP_SECONDS = 5


def _to_risk_item(
    spotted: SpottedVideoItem,
    offset_seconds: int,
    window_seconds: int | None = None,
) -> RiskItem | None:
    try:
        local_start = parse_timecode(spotted.start_timecode)
        local_end = parse_timecode(spotted.end_timecode)
    except ValueError:
        logger.warning(
            "Discarding item %r with unparseable timecodes %r-%r",
            spotted.title,
            spotted.start_timecode,
            spotted.end_timecode,
        )
        return None

    if local_end < local_start:
        local_start, local_end = local_end, local_start

    # Timecodes are relative to the window the model was shown, so anything
    # past the end of that window is not a location in the cut — it is the
    # model guessing. Left alone it produces a schedule that sends a reviewer
    # to 16:08 of a five-minute film, and a letter quoting an eleven-minute
    # appearance. Clamp it back into the window and say so, rather than
    # discarding an item that is genuinely in the picture somewhere.
    out_of_range = False
    raw = f"{spotted.start_timecode.strip()}–{spotted.end_timecode.strip()}"
    if window_seconds:
        limit = window_seconds + _TIMECODE_SLOP_SECONDS
        if local_start > limit or local_end > limit:
            out_of_range = True
            logger.warning(
                "Item %r reported %s in a %ss window; clamping",
                spotted.title,
                raw,
                window_seconds,
            )
            local_start = min(local_start, window_seconds)
            local_end = min(local_end, window_seconds)
            if local_end < local_start:
                local_start, local_end = local_end, local_start

    start = local_start + offset_seconds
    end = local_end + offset_seconds

    return RiskItem(
        id=f"itm_{uuid.uuid4().hex[:10]}",
        category=_demote_unverifiable_library_music(spotted.category),
        title=spotted.title.strip() or "Unnamed item",
        description=spotted.description.strip(),
        source=Source.VIDEO,
        start_seconds=start,
        end_seconds=end,
        prominence=spotted.prominence,
        identifiability=spotted.identifiability,
        audible_in_clear=spotted.audible_in_clear,
        depicted_negatively=spotted.depicted_negatively,
        no_published_identity=spotted.no_published_identity,
        detection_confidence=(
            Confidence.LOW if out_of_range else spotted.detection_confidence
        ),
        evidence=_evidence_with_note(spotted.evidence, out_of_range, raw, window_seconds),
        known_work=spotted.known_work.strip() or None,
        known_year=_safe_year(spotted.known_year),
    )


def _evidence_with_note(
    evidence: str,
    out_of_range: bool,
    raw_timecode: str = "",
    window_seconds: int | None = None,
) -> str | None:
    """Attach the model's own words when its timing had to be overruled.

    The raw string is kept deliberately. Reading "16:08" as sixteen minutes
    rather than sixteen seconds changes where an item sits by an order of
    magnitude, and once it has been parsed the original is gone — leaving no
    way to tell a model that lost track of the clip from a model using a
    format nobody asked for. Quoting it makes that answerable.
    """
    text = (evidence or "").strip()
    if not out_of_range:
        return text or None
    note = (
        f"Model reported {raw_timecode} for a {window_seconds}s segment; "
        "timing has been clamped to the segment. Confirm where this actually "
        "sits before relying on it."
    )
    return f"{text.rstrip('.')}. {note}" if text else note


def _script_to_risk_item(spotted: SpottedScriptItem) -> RiskItem:
    return RiskItem(
        id=f"itm_{uuid.uuid4().hex[:10]}",
        category=spotted.category,
        title=spotted.title.strip() or "Unnamed item",
        description=spotted.description.strip(),
        source=Source.SCRIPT,
        scene=spotted.scene.strip() or None,
        prominence=spotted.prominence,
        identifiability=Identifiability.CLEARLY_IDENTIFIABLE,
        depicted_negatively=spotted.depicted_negatively,
        detection_confidence=spotted.detection_confidence,
        evidence=spotted.evidence.strip() or None,
        known_work=spotted.known_work.strip() or None,
        known_year=_safe_year(spotted.known_year),
    )


# ---------------------------------------------------------------------------
# Spotter
# ---------------------------------------------------------------------------


class Spotter:
    """Gemini-backed spotting over picture and script."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - import guard
            raise RuntimeError(
                "google-genai is required. pip install google-genai"
            ) from exc

        if self.settings.google_genai_use_vertexai:
            if not self.settings.google_cloud_project:
                raise RuntimeError(
                    "GOOGLE_CLOUD_PROJECT must be set to use Vertex AI. "
                    "Set it in .env."
                )
            self._client = genai.Client(
                vertexai=True,
                project=self.settings.google_cloud_project,
                location=self.settings.google_cloud_location,
            )
        else:
            self._client = genai.Client()

        self.model = self.settings.spotter_model

    # -- picture ------------------------------------------------------------

    def spot_video(
        self,
        project: ProjectMeta,
        *,
        gcs_uri: str | None = None,
        local_path: str | Path | None = None,
        mime_type: str = "video/mp4",
        max_workers: int = 3,
    ) -> list[RiskItem]:
        """Analyse a cut and return every observation, on the master timeline."""
        if not gcs_uri and not local_path:
            raise ValueError("provide either gcs_uri or local_path")

        windows = plan_windows(project.runtime_seconds)
        logger.info(
            "Spotting %s across %d window(s)", project.title, len(windows)
        )

        video_bytes: bytes | None = None
        if local_path is not None:
            video_bytes = Path(local_path).read_bytes()

        results: list[RiskItem] = []
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(max_workers, len(windows))
        ) as pool:
            futures = {
                pool.submit(
                    self._spot_window,
                    project,
                    window,
                    gcs_uri,
                    video_bytes,
                    mime_type,
                ): window
                for window in windows
            }
            for future in concurrent.futures.as_completed(futures):
                window = futures[future]
                try:
                    results.extend(future.result())
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "Spotting failed for window %s-%s", window.start, window.end
                    )

        return deduplicate(results)

    def _spot_window(
        self,
        project: ProjectMeta,
        window: Window,
        gcs_uri: str | None,
        video_bytes: bytes | None,
        mime_type: str,
    ) -> list[RiskItem]:
        from google.genai import types

        metadata = types.VideoMetadata(fps=ANALYSIS_FPS)
        if window.duration:
            metadata.start_offset = f"{window.start}s"
            metadata.end_offset = f"{window.end}s"

        if gcs_uri:
            media_part = types.Part(
                file_data=types.FileData(file_uri=gcs_uri, mime_type=mime_type),
                video_metadata=metadata,
            )
        else:
            media_part = types.Part(
                inline_data=types.Blob(data=video_bytes, mime_type=mime_type),
                video_metadata=metadata,
            )

        response = self._client.models.generate_content(
            model=self.model,
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        media_part,
                        types.Part(text=_video_prompt(project, window.start, window.end)),
                    ],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=VideoSpotResponse,
                # Spotting is an observation task; sampling variance here shows
                # up as items appearing and vanishing between runs.
                temperature=0.0,
            ),
        )

        parsed = _parse_response(response, VideoSpotResponse)
        items = []
        for spotted in parsed.items:
            item = _to_risk_item(spotted, window.start, window.duration or None)
            if item is not None:
                items.append(item)
        return items

    # -- script -------------------------------------------------------------

    def spot_script(self, project: ProjectMeta, script_text: str) -> list[RiskItem]:
        from google.genai import types

        if not script_text.strip():
            return []

        response = self._client.models.generate_content(
            model=self.model,
            contents=(
                f"Production: '{project.title}'.\n"
                f"Intended distribution: {project.distribution_intent}.\n\n"
                "Read the screenplay below and record every element that will "
                "need clearing.\n\n"
                "--- SCREENPLAY ---\n"
                f"{script_text}"
            ),
            config=types.GenerateContentConfig(
                system_instruction=_SCRIPT_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=ScriptSpotResponse,
                temperature=0.0,
            ),
        )
        parsed = _parse_response(response, ScriptSpotResponse)
        return deduplicate([_script_to_risk_item(s) for s in parsed.items])


def _parse_response(response: Any, schema: type[BaseModel]) -> Any:
    """Prefer the SDK's parsed object; fall back to the raw JSON text."""
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, schema):
        return parsed
    text = getattr(response, "text", None)
    if not text:
        return schema(items=[])
    try:
        return schema.model_validate(json.loads(text))
    except (json.JSONDecodeError, ValueError):
        logger.exception("Could not parse spotter response")
        return schema(items=[])


# ---------------------------------------------------------------------------
# Script ingestion
# ---------------------------------------------------------------------------


def read_script(path: str | Path) -> str:
    """Read a screenplay from PDF or plain text."""
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("pypdf is required to read PDF scripts") from exc
        reader = PdfReader(str(path))
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    return path.read_text(encoding="utf-8", errors="replace")


def merge_sources(*groups: Iterable[RiskItem]) -> list[RiskItem]:
    """Combine picture and script observations into one worklist."""
    combined: list[RiskItem] = []
    for group in groups:
        combined.extend(group)
    return deduplicate(combined)
