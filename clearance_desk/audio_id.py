"""Naming the music, so research has something to research.

The picture pass can hear that music is playing and where, but it cannot say
what the track is — that needs acoustic fingerprinting against a catalogue of
released recordings. Without a name the chain stops dead: the rules engine
blocks the item and asks the production for a cue sheet, which is honest but
unhelpful when the cue is a commercial needle-drop somebody could simply have
looked up.

This module closes that gap. A match turns "unnamed track at 14:22" into
"Ain't No Sunshine (1971)", which is exactly the input the research stage needs
to go and find the publisher and the master owner. Fingerprinting on its own
identifies nothing about ownership; its value is entirely in unblocking the
step after it.

What it cannot do
-----------------
Fingerprinting matches *released* recordings. A score composed for the
production is in no catalogue and never will be, so it stays unidentified and
``ID-001`` still asks for the cue sheet. This addresses needle-drops — which is
where the money is, since those are the two-sided clearances — not original
music.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings, get_settings
from .models import Category, RiskItem

logger = logging.getLogger(__name__)

#: Enough audio to fingerprint reliably without pushing at the upload limit.
SAMPLE_SECONDS = 20

#: Below roughly this, a fingerprint is unlikely to match anything. Short stabs
#: and stings are sampled anyway — a miss costs one lookup and is reported
#: honestly — but the window is never padded out past the cue to reach it.
MIN_SAMPLE_SECONDS = 4

#: AudD rejects payloads above 10 MB on the standard endpoint.
MAX_UPLOAD_BYTES = 9 * 1024 * 1024


@dataclass
class MusicMatch:
    title: str
    artist: str | None = None
    album: str | None = None
    release_date: str | None = None
    label: str | None = None
    song_link: str | None = None
    provider: str = "audd"

    @property
    def year(self) -> int | None:
        if not self.release_date:
            return None
        head = self.release_date[:4]
        return int(head) if head.isdigit() else None

    def as_work_title(self) -> str:
        """The phrase handed on to research."""
        return f"{self.title} — {self.artist}" if self.artist else self.title


# ---------------------------------------------------------------------------
# Audio extraction
# ---------------------------------------------------------------------------


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_audio_segment(
    source: str | Path,
    start_seconds: int,
    duration_seconds: int = SAMPLE_SECONDS,
) -> bytes | None:
    """Cut a short mono MP3 out of a cut, for fingerprinting.

    Returns None rather than raising when ffmpeg is missing or the cut cannot
    be read — an absent fingerprint degrades to "unidentified", which the rules
    engine already handles correctly.
    """
    if not ffmpeg_available():
        logger.warning("ffmpeg not on PATH; skipping acoustic identification")
        return None

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "segment.mp3"
        command = [
            "ffmpeg",
            "-nostdin",
            "-loglevel", "error",
            "-ss", str(max(0, start_seconds)),
            "-t", str(max(1, duration_seconds)),
            "-i", str(source),
            "-vn",              # drop the picture
            "-ac", "1",         # mono is plenty for fingerprinting
            "-ar", "22050",
            "-b:a", "96k",
            "-y", str(out),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=120)
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "ffmpeg failed on %s at %ss: %s",
                source,
                start_seconds,
                (exc.stderr or b"").decode("utf-8", "replace")[:200],
            )
            return None
        except (subprocess.TimeoutExpired, OSError):
            logger.exception("ffmpeg could not extract audio from %s", source)
            return None

        if not out.exists() or out.stat().st_size == 0:
            return None
        data = out.read_bytes()

    if len(data) > MAX_UPLOAD_BYTES:
        logger.warning("audio segment too large to fingerprint (%d bytes)", len(data))
        return None
    return data


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


class AudioIdentifier(Protocol):
    name: str

    def identify(self, audio: bytes) -> MusicMatch | None: ...


class AuddIdentifier:
    """Acoustic fingerprinting via AudD."""

    name = "audd"
    ENDPOINT = "https://api.audd.io/"

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        if not self.settings.audd_api_token:
            raise RuntimeError("AUDD_API_TOKEN is not set")

    def identify(self, audio: bytes) -> MusicMatch | None:
        import httpx

        try:
            response = httpx.post(
                self.ENDPOINT,
                data={"api_token": self.settings.audd_api_token},
                files={"file": ("segment.mp3", audio, "audio/mpeg")},
                timeout=60.0,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:  # noqa: BLE001 - never fail a run over a lookup
            logger.exception("AudD lookup failed")
            return None

        if payload.get("status") != "success":
            logger.warning("AudD returned %s", payload.get("error") or payload.get("status"))
            return None

        result = payload.get("result")
        if not result:
            return None  # no match — a normal outcome, not an error
        title = (result.get("title") or "").strip()
        if not title:
            return None

        return MusicMatch(
            title=title,
            artist=(result.get("artist") or "").strip() or None,
            album=(result.get("album") or "").strip() or None,
            release_date=(result.get("release_date") or "").strip() or None,
            label=(result.get("label") or "").strip() or None,
            song_link=(result.get("song_link") or "").strip() or None,
        )


class NullIdentifier:
    """Used when no fingerprinting service is configured."""

    name = "none"

    def identify(self, audio: bytes) -> MusicMatch | None:  # noqa: ARG002
        return None


def get_identifier(settings: Settings | None = None) -> AudioIdentifier:
    settings = settings or get_settings()
    if settings.audd_api_token:
        return AuddIdentifier(settings)
    return NullIdentifier()


# ---------------------------------------------------------------------------
# Naming the cues
# ---------------------------------------------------------------------------


def sample_window(item: RiskItem) -> tuple[int, int]:
    """Where to cut the fingerprinting sample from, and how long to make it.

    Two competing concerns. The opening seconds of a cue are often a fade, or
    buried under dialogue, so starting slightly late gives a cleaner sample.
    But a short cue must not be over-run: sampling twenty seconds of a four
    second stab drags in whatever follows and dilutes the fingerprint to the
    point where it stops matching.

    So the window is clamped to the cue itself.
    """
    start = item.start_seconds or 0
    duration = item.duration_seconds

    if duration is None:
        return start, SAMPLE_SECONDS

    # Skip a lead-in only when there is enough cue left to be worth it.
    lead_in = 2 if duration > 8 else 0
    usable = max(MIN_SAMPLE_SECONDS, duration - lead_in)
    return start + lead_in, min(SAMPLE_SECONDS, usable)


def needs_identification(item: RiskItem) -> bool:
    """Only unnamed music, and only where we have a timecode to sample."""
    return (
        item.category is Category.MUSIC_SYNC
        and not item.known_work
        and item.start_seconds is not None
    )


def identify_music(
    items: list[RiskItem],
    source: str | Path | None,
    identifier: AudioIdentifier | None = None,
    settings: Settings | None = None,
) -> dict[str, MusicMatch]:
    """Name the unnamed cues in place.

    Matched items have ``known_work`` and ``known_year`` filled in, which is
    what lets the research stage go looking for a publisher rather than
    reporting the cue as unidentifiable.
    """
    settings = settings or get_settings()
    identifier = identifier or get_identifier(settings)

    candidates = [i for i in items if needs_identification(i)]
    if not candidates or source is None or isinstance(identifier, NullIdentifier):
        return {}

    if not ffmpeg_available():
        logger.warning(
            "%d unnamed cue(s) but ffmpeg is unavailable; leaving them unidentified",
            len(candidates),
        )
        return {}

    matches: dict[str, MusicMatch] = {}
    for item in candidates:
        offset, length = sample_window(item)
        audio = extract_audio_segment(source, offset, length)
        if audio is None:
            continue

        match = identifier.identify(audio)
        if match is None:
            logger.info("no acoustic match for cue at %s", item.timecode)
            continue

        item.known_work = match.as_work_title()
        if match.year:
            item.known_year = match.year

        # Always record how the work got its name. The spotter's own account of
        # a cue is a guess about what it sounds like, and once a fingerprint has
        # named the work that guess is superseded — sometimes flatly
        # contradicted. A reader comparing the two needs to know which one is
        # evidence and which is impression.
        provenance = f"Identified acoustically as {match.as_work_title()}"
        if match.label:
            provenance += f"; label of record {match.label}"
        item.evidence = (
            f"{item.evidence.rstrip('.')}. {provenance}."
            if item.evidence
            else f"{provenance}."
        )
        matches[item.id] = match
        logger.info("cue at %s identified as %s", item.timecode, match.as_work_title())

    return matches
