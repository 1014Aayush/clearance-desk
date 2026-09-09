"""Accepting a cut from the browser.

Two storage paths, because the right one depends on where this is running.

A cut staged in Cloud Storage is what production wants: Gemini reads it by URI,
nothing large crosses the request boundary, and a feature-length file is fine.
A cut held on local disk is what a laptop wants: no bucket to configure, no
credentials beyond the ones already present, and it is inlined into the model
request — which caps it at roughly twenty megabytes.

The caller never names a path. Uploads are addressed by an opaque handle that
the server resolves, so a crafted request cannot point the pipeline at an
arbitrary file on disk.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from .config import Settings, get_settings

logger = logging.getLogger(__name__)

#: Inline request payloads are capped well below this; beyond it a bucket is
#: required rather than optional.
INLINE_LIMIT_BYTES = 18 * 1024 * 1024

ALLOWED_SUFFIXES = {".mp4", ".mov", ".webm", ".mpeg", ".mpg", ".avi", ".mkv"}

_MIME_BY_SUFFIX = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}


def probe_duration(path: Path) -> int | None:
    """Runtime of a cut, in whole seconds.

    Needed for two things that both fail quietly without it: the timeline has
    nothing to lay items out against, and — more seriously — the spotting pass
    cannot plan its windows, so a feature-length cut goes to the model as a
    single request instead of twenty overlapping ones.
    """
    if shutil.which("ffprobe") is None:
        logger.warning("ffprobe not on PATH; runtime will be unknown")
        return None
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return int(float(result.stdout.strip()))
    except (subprocess.SubprocessError, ValueError, OSError):
        logger.warning("could not read duration from %s", path.name)
        return None


@dataclass
class Upload:
    handle: str
    filename: str
    size_bytes: int
    mime_type: str
    duration_seconds: int | None = None
    gcs_uri: str | None = None
    local_path: Path | None = None

    @property
    def location(self) -> str:
        return self.gcs_uri or "local disk"


class UploadStore:
    """Holds uploaded cuts for the lifetime of the process."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._uploads: dict[str, Upload] = {}
        self._dir = Path(tempfile.gettempdir()) / "clearance_desk_uploads"
        self._dir.mkdir(parents=True, exist_ok=True)

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _safe_name(filename: str) -> str:
        stem = Path(filename or "cut").name
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._") or "cut"
        return cleaned[:120]

    @staticmethod
    def suffix_of(filename: str) -> str:
        return Path(filename or "").suffix.lower()

    # -- ingest -------------------------------------------------------------

    def accept(self, filename: str, stream) -> Upload:  # noqa: ANN001
        """Store an uploaded cut and return a handle for it."""
        suffix = self.suffix_of(filename)
        if suffix not in ALLOWED_SUFFIXES:
            raise ValueError(
                f"unsupported file type {suffix or '(none)'}; "
                f"expected one of {', '.join(sorted(ALLOWED_SUFFIXES))}"
            )

        handle = uuid.uuid4().hex[:12]
        safe = self._safe_name(filename)
        staged = self._dir / f"{handle}_{safe}"

        with staged.open("wb") as out:
            shutil.copyfileobj(stream, out, length=1024 * 1024)
        size = staged.stat().st_size

        if size == 0:
            staged.unlink(missing_ok=True)
            raise ValueError("uploaded file is empty")

        mime = _MIME_BY_SUFFIX.get(suffix, "video/mp4")
        # Probed while the file is still on local disk — it may be about to
        # move to a bucket, where ffprobe cannot reach it.
        duration = probe_duration(staged)
        bucket = self.settings.gcs_bucket

        bucket_failed = False
        if bucket:
            try:
                gcs_uri = self._to_gcs(bucket, staged, handle, safe, mime)
                upload = Upload(handle, safe, size, mime, duration, gcs_uri=gcs_uri)
                staged.unlink(missing_ok=True)
                self._uploads[handle] = upload
                logger.info("staged %s (%d bytes) at %s", safe, size, gcs_uri)
                return upload
            except Exception:  # noqa: BLE001
                # A bucket that is misconfigured should not lose the upload —
                # fall back to disk and let the size check decide.
                bucket_failed = True
                logger.exception("GCS upload failed; falling back to local disk")

        if size > INLINE_LIMIT_BYTES:
            staged.unlink(missing_ok=True)
            limit = INLINE_LIMIT_BYTES / 1e6
            # Distinguish "no bucket" from "bucket named but unreachable".
            # Telling someone to set a variable they have already set sends
            # them looking in the wrong place entirely.
            if bucket_failed:
                raise ValueError(
                    f"file is {size / 1e6:.0f} MB, and the configured bucket "
                    f"'{bucket}' could not be written to — check it still "
                    f"exists and that this service can reach it. Files above "
                    f"{limit:.0f} MB cannot be analysed without it."
                )
            raise ValueError(
                f"file is {size / 1e6:.0f} MB. Without a Cloud Storage bucket "
                f"the cut must be inlined into the model request, which caps it "
                f"at {limit:.0f} MB. Set GCS_BUCKET to analyse longer cuts."
            )

        upload = Upload(handle, safe, size, mime, duration, local_path=staged)
        self._uploads[handle] = upload
        logger.info("staged %s (%d bytes) on local disk", safe, size)
        return upload

    def _to_gcs(
        self, bucket: str, staged: Path, handle: str, safe: str, mime: str
    ) -> str:
        from google.cloud import storage

        client = storage.Client(project=self.settings.google_cloud_project or None)
        blob = client.bucket(bucket).blob(f"cuts/{handle}/{safe}")
        blob.upload_from_filename(str(staged), content_type=mime)
        return f"gs://{bucket}/{blob.name}"

    # -- lookup -------------------------------------------------------------

    def get(self, handle: str) -> Upload | None:
        return self._uploads.get(handle)

    def resolve(self, handle: str) -> tuple[str | None, Path | None, str]:
        """Resolve a handle to (gcs_uri, local_path, mime_type)."""
        upload = self._uploads.get(handle)
        if upload is None:
            raise KeyError(handle)
        return upload.gcs_uri, upload.local_path, upload.mime_type
