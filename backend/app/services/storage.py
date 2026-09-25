"""Local filesystem storage for uploads.

Stored names are UUIDs, so a malicious original name can never influence the
path on disk. The original name is kept only as a display label, sanitised
first.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._ -]+")
_MULTI_DOT_RE = re.compile(r"\.{2,}")
MAX_DISPLAY_NAME = 120


class StorageError(RuntimeError):
    """A file could not be written, read or removed."""


def sanitise_filename(name: str) -> str:
    """Make an uploaded filename safe to store and display.

    Strips directory components, normalises Unicode, removes anything outside a
    conservative allowlist, and collapses the ``..`` sequences used for path
    traversal.

    Args:
        name: The client-supplied filename.

    Returns:
        A safe display name, never empty.

    Example:
        >>> sanitise_filename("../../etc/passwd")
        'passwd'
    """
    base = Path(name.replace("\\", "/")).name
    normalised = unicodedata.normalize("NFKD", base)
    cleaned = _UNSAFE_NAME_RE.sub("_", normalised).strip(" ._")
    cleaned = _MULTI_DOT_RE.sub(".", cleaned)
    if not cleaned:
        cleaned = "upload"
    return cleaned[:MAX_DISPLAY_NAME]


def build_stored_name(original_name: str) -> str:
    """Return a collision-proof storage name preserving the extension."""
    suffix = Path(sanitise_filename(original_name)).suffix.lower()
    if len(suffix) > 10:
        suffix = ""
    return f"{uuid.uuid4().hex}{suffix}"


class LocalStorage:
    """Stores uploads under a configurable directory."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        """Create the storage backend, ensuring the directory exists."""
        self._base = Path(base_dir) if base_dir else settings.upload_path
        try:
            self._base.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageError(f"Could not create upload directory {self._base}: {exc}") from exc

    @property
    def base_dir(self) -> Path:
        """The directory uploads are written to."""
        return self._base

    def path_for(self, stored_name: str) -> Path:
        """Resolve ``stored_name`` inside the upload directory.

        Raises:
            StorageError: The name escapes the upload directory.
        """
        candidate = (self._base / stored_name).resolve()
        base = self._base.resolve()
        if base != candidate and base not in candidate.parents:
            raise StorageError("Refusing to access a path outside the upload directory.")
        return candidate

    async def write(self, stored_name: str, data: bytes) -> Path:
        """Write ``data`` to ``stored_name``.

        Returns:
            The path written.

        Raises:
            StorageError: The write failed.
        """
        path = self.path_for(stored_name)
        try:
            await asyncio.to_thread(path.write_bytes, data)
        except OSError as exc:
            logger.exception("Failed to write upload %s.", stored_name)
            raise StorageError(f"Could not save the file: {exc}") from exc
        logger.info("Stored upload %s (%d bytes).", stored_name, len(data))
        return path

    async def read(self, stored_name: str) -> bytes:
        """Read a stored file's bytes.

        Raises:
            StorageError: The file is missing or unreadable.
        """
        path = self.path_for(stored_name)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except FileNotFoundError as exc:
            raise StorageError("That file is no longer on disk.") from exc
        except OSError as exc:
            raise StorageError(f"Could not read the file: {exc}") from exc

    async def delete(self, stored_name: str) -> bool:
        """Delete a stored file.

        Returns:
            True when a file was removed, False when it was already gone.
        """
        path = self.path_for(stored_name)

        def _unlink() -> bool:
            try:
                path.unlink()
                return True
            except FileNotFoundError:
                return False

        try:
            removed = await asyncio.to_thread(_unlink)
        except OSError as exc:
            logger.warning("Could not delete %s: %s", stored_name, exc)
            raise StorageError(f"Could not delete the file: {exc}") from exc
        if removed:
            logger.info("Deleted upload %s.", stored_name)
        return removed


_storage: Optional[LocalStorage] = None


def get_storage() -> LocalStorage:
    """Return the process-wide :class:`LocalStorage`."""
    global _storage
    if _storage is None:
        _storage = LocalStorage()
    return _storage
