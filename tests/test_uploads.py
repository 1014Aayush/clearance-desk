"""Tests for accepting a cut from the browser.

The security-relevant property here is that a client never names a filesystem
path — it gets an opaque handle back and the server resolves it. Everything
else is about failing clearly rather than obscurely.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from clearance_desk.config import Settings
from clearance_desk.uploads import INLINE_LIMIT_BYTES, UploadStore


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> UploadStore:
    s = UploadStore(Settings(gcs_bucket="", google_cloud_project=""))
    monkeypatch.setattr(s, "_dir", tmp_path)
    return s


def test_accepts_a_video_and_returns_a_handle(store: UploadStore) -> None:
    upload = store.accept("rough cut v4.mp4", io.BytesIO(b"\x00" * 2048))
    assert upload.handle
    assert upload.size_bytes == 2048
    assert upload.mime_type == "video/mp4"
    assert upload.local_path is not None and upload.local_path.exists()


def test_handle_resolves_server_side(store: UploadStore) -> None:
    upload = store.accept("cut.mp4", io.BytesIO(b"\x00" * 16))
    gcs_uri, local_path, mime = store.resolve(upload.handle)
    assert gcs_uri is None
    assert local_path is not None
    assert mime == "video/mp4"


def test_unknown_handle_raises(store: UploadStore) -> None:
    with pytest.raises(KeyError):
        store.resolve("nope")


@pytest.mark.parametrize("name", ["notes.txt", "script.pdf", "archive.zip", "noext"])
def test_non_video_is_rejected(store: UploadStore, name: str) -> None:
    with pytest.raises(ValueError, match="unsupported file type"):
        store.accept(name, io.BytesIO(b"data"))


def test_empty_file_is_rejected(store: UploadStore) -> None:
    with pytest.raises(ValueError, match="empty"):
        store.accept("cut.mp4", io.BytesIO(b""))


def test_oversized_file_without_a_bucket_explains_the_fix(store: UploadStore) -> None:
    """The message has to name the remedy, not just the limit."""
    big = io.BytesIO(b"\x00" * (INLINE_LIMIT_BYTES + 1024))
    with pytest.raises(ValueError, match="GCS_BUCKET"):
        store.accept("feature.mp4", big)


def test_oversized_file_is_not_left_on_disk(store: UploadStore, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        store.accept("feature.mp4", io.BytesIO(b"\x00" * (INLINE_LIMIT_BYTES + 1024)))
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# Filename handling — a client-supplied name must not escape the staging dir
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "given",
    [
        "../../../etc/passwd.mp4",
        "..\\..\\windows\\system32\\evil.mp4",
        "/absolute/path/cut.mp4",
        "cut;rm -rf.mp4",
        "  spaced name .mp4",
    ],
)
def test_hostile_filenames_are_neutralised(
    store: UploadStore, tmp_path: Path, given: str
) -> None:
    upload = store.accept(given, io.BytesIO(b"\x00" * 32))
    assert upload.local_path is not None
    # Whatever the client called it, it lands inside the staging directory.
    assert upload.local_path.parent == tmp_path
    assert ".." not in upload.filename
    assert "/" not in upload.filename and "\\" not in upload.filename


def test_long_filenames_are_truncated(store: UploadStore) -> None:
    upload = store.accept("x" * 400 + ".mp4", io.BytesIO(b"\x00" * 32))
    assert len(upload.filename) <= 120


@pytest.mark.parametrize(
    "name,mime",
    [
        ("cut.mp4", "video/mp4"),
        ("cut.mov", "video/quicktime"),
        ("cut.webm", "video/webm"),
        ("cut.mkv", "video/x-matroska"),
    ],
)
def test_mime_types_are_mapped(store: UploadStore, name: str, mime: str) -> None:
    assert store.accept(name, io.BytesIO(b"\x00" * 32)).mime_type == mime
