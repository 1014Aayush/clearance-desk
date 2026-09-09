"""Bundled sample cuts, so a tester never needs footage of their own.

The tool asks for a video before it can say anything at all, which is a poor
first minute for anyone evaluating it: they have to go and find a clip, and
whatever they find is usually a phone video of nothing in particular, which
produces an empty report and reads as the system failing.

So five short cuts ship with the application. Each one is a sixty-second
excerpt of a public-domain sponsored film, chosen because it is *dense* — a
minute that contains brands, music, artwork and faces at the same time, which
is what makes a clearance report worth reading. Provenance and the basis for
the public-domain claim travel with them in ``manifest.json`` and the README
beside it; a rights tool that shipped footage it could not account for would be
making the argument against itself.

Like the worked example, each sample carries a recorded run
(see :mod:`clearance_desk.demo_cache`, whose format this reuses). Research is
billed per request, so a sample that researched live on every visit would
charge the account hosting the page once per tester. The recording is replayed
instead, and live is a deliberate choice behind a confirmation.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .demo_cache import load_demo_run, save_demo_run
from .models import ClearanceReport, ProgressEvent

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"


def samples_dir() -> Path:
    """Where the bundled cuts live.

    ``assets/`` sits beside the package rather than inside it, and the
    Dockerfile copies it explicitly — media is not Python and does not belong
    in the importable tree. ``SAMPLES_DIR`` overrides both, which is what the
    tests use.
    """
    override = os.environ.get("SAMPLES_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent.parent / "assets" / "samples"


@dataclass(frozen=True)
class Sample:
    """One bundled cut and the paperwork that comes with it."""

    id: str
    title: str
    year: int | None
    producer: str | None
    sponsor: str | None
    blurb: str
    exercises: str
    excerpt: str | None
    runtime_seconds: int | None
    source_url: str
    rights: str
    video: str
    poster: str | None
    cue_sheet: str | None
    recording: str | None
    directory: Path

    @property
    def video_path(self) -> Path:
        return self.directory / self.video

    @property
    def poster_path(self) -> Path | None:
        return self.directory / self.poster if self.poster else None

    @property
    def cue_sheet_path(self) -> Path | None:
        return self.directory / self.cue_sheet if self.cue_sheet else None

    @property
    def recording_path(self) -> Path | None:
        return self.directory / self.recording if self.recording else None

    def has_video(self) -> bool:
        return self.video_path.is_file()

    def has_poster(self) -> bool:
        path = self.poster_path
        return path is not None and path.is_file()

    def has_cue_sheet(self) -> bool:
        path = self.cue_sheet_path
        return path is not None and path.is_file()

    def has_recording(self) -> bool:
        path = self.recording_path
        return path is not None and path.is_file()

    def as_dict(self) -> dict[str, object]:
        """The shape the browser receives. Paths deliberately never leave."""
        return {
            "id": self.id,
            "title": self.title,
            "year": self.year,
            "producer": self.producer,
            "sponsor": self.sponsor,
            "blurb": self.blurb,
            "exercises": self.exercises,
            "excerpt": self.excerpt,
            "runtime_seconds": self.runtime_seconds,
            "source_url": self.source_url,
            "rights": self.rights,
            "has_poster": self.has_poster(),
            "has_cue_sheet": self.has_cue_sheet(),
            "has_recording": self.has_recording(),
        }


def load_samples(directory: Path | None = None) -> list[Sample]:
    """Read the manifest.

    A missing manifest is not an error — an install without the assets
    directory simply has no samples to offer, and the upload path still works.
    A sample whose video is absent is dropped rather than advertised, because
    the failure would otherwise land on the tester as a broken button.
    """
    directory = directory or samples_dir()
    manifest = directory / MANIFEST_NAME
    if not manifest.is_file():
        logger.info("no sample manifest at %s", manifest)
        return []

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("could not read sample manifest %s", manifest)
        return []

    samples: list[Sample] = []
    for raw in payload.get("samples", []):
        try:
            sample = Sample(
                id=raw["id"],
                title=raw["title"],
                year=raw.get("year"),
                producer=raw.get("producer"),
                sponsor=raw.get("sponsor"),
                blurb=raw.get("blurb", ""),
                exercises=raw.get("exercises", ""),
                excerpt=raw.get("excerpt"),
                runtime_seconds=raw.get("runtime_seconds"),
                source_url=raw.get("source_url", ""),
                rights=raw.get("rights", ""),
                video=raw["video"],
                poster=raw.get("poster"),
                cue_sheet=raw.get("cue_sheet"),
                recording=raw.get("recording"),
                directory=directory,
            )
        except KeyError:
            logger.exception("skipping malformed sample entry in %s", manifest)
            continue

        if not sample.has_video():
            logger.warning(
                "sample %s listed but %s is missing; not offering it",
                sample.id,
                sample.video_path,
            )
            continue
        samples.append(sample)

    return samples


def get_sample(sample_id: str, directory: Path | None = None) -> Sample | None:
    for sample in load_samples(directory):
        if sample.id == sample_id:
            return sample
    return None


def load_sample_recording(
    sample: Sample,
) -> tuple[ClearanceReport, list[ProgressEvent]] | None:
    """The recorded run for this sample, or None if it has never been recorded."""
    path = sample.recording_path
    if path is None or not path.is_file():
        return None
    return load_demo_run(path)


def save_sample_recording(
    sample: Sample,
    report: ClearanceReport,
    events: list[ProgressEvent],
) -> Path:
    """Record a live pass over this sample for everyone who comes after."""
    path = sample.recording_path
    if path is None:
        raise ValueError(f"sample {sample.id} declares no recording file")
    return save_demo_run(report, events, path)
