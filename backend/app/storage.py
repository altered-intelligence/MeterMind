"""Pluggable storage for uploaded bill PDFs.

The MVP uses :class:`LocalStorage` (filesystem). The :class:`Storage` protocol is the
seam for adding S3-compatible object storage later without touching the rest of the app.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from .config import get_settings


@runtime_checkable
class Storage(Protocol):
    """Abstract blob storage. Implementations must not log file contents."""

    def save(self, data: bytes, *, suffix: str = ".pdf") -> str:
        """Persist ``data`` and return an opaque storage key."""

    def path(self, key: str) -> str:
        """Return a local filesystem path for ``key`` (for parsing)."""

    def delete(self, key: str) -> None:
        ...


class LocalStorage:
    """Stores blobs under ``settings.storage_dir`` with random, non-PII filenames."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = Path(base_dir or get_settings().storage_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def save(self, data: bytes, *, suffix: str = ".pdf") -> str:
        # Random key — never derived from the original filename or account data.
        key = f"{uuid.uuid4().hex}{suffix}"
        (self.base_dir / key).write_bytes(data)
        return key

    def path(self, key: str) -> str:
        return str(self.base_dir / key)

    def delete(self, key: str) -> None:
        target = self.base_dir / key
        if target.exists():
            target.unlink()


# TODO(future): S3Storage(Storage) backed by boto3 / an S3-compatible endpoint.
# class S3Storage:
#     def __init__(self, bucket, client): ...
#     def save(self, data, *, suffix=".pdf") -> str:  # returns s3 key
#     def path(self, key) -> str:  # download to a temp file and return its path
#     def delete(self, key) -> None: ...


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage
