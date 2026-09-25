"""Upload orchestration: validate, store, extract, chunk, persist.

Validation is allowlist-based on both the extension and the declared MIME type,
and the two must agree -- a ``.pdf`` announced as ``text/plain`` is rejected.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import FileChunk, FileStatus, UploadedFile
from app.services.storage import LocalStorage, StorageError, build_stored_name, get_storage, sanitise_filename
from app.services.text_extract import ExtractionError, extract

logger = logging.getLogger(__name__)

#: Extension -> acceptable MIME types.
ALLOWED_TYPES: dict[str, tuple[str, ...]] = {
    ".pdf": ("application/pdf",),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
    ".txt": ("text/plain",),
    ".md": ("text/markdown", "text/plain", "text/x-markdown"),
    ".markdown": ("text/markdown", "text/plain", "text/x-markdown"),
    ".png": ("image/png",),
    ".jpg": ("image/jpeg",),
    ".jpeg": ("image/jpeg",),
    ".webp": ("image/webp",),
}

IMAGE_EXTENSIONS: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".webp"})

#: Magic-number prefixes used as a cheap sanity check on the declared type.
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF-",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".docx": (b"PK\x03\x04",),
    ".webp": (b"RIFF",),
}


class FileValidationError(ValueError):
    """The upload was rejected before anything was written to disk."""


@dataclass(slots=True)
class StoredUpload:
    """The result of a successful upload."""

    file: UploadedFile
    chunk_count: int
    is_image: bool


def validate_upload(original_name: str, mime: str, size_bytes: int) -> tuple[str, str]:
    """Validate an upload's name, type and size.

    Args:
        original_name: Client-supplied filename.
        mime: Client-supplied content type.
        size_bytes: Size of the payload.

    Returns:
        ``(safe_name, extension)``.

    Raises:
        FileValidationError: The upload violates the allowlist or size cap.
    """
    safe_name = sanitise_filename(original_name)
    extension = Path(safe_name).suffix.lower()

    if extension not in ALLOWED_TYPES:
        raise FileValidationError(
            f"'{extension or 'no extension'}' files aren't supported. "
            f"Allowed: {', '.join(sorted(ALLOWED_TYPES))}."
        )

    declared = (mime or "").split(";")[0].strip().lower()
    if declared and declared not in ALLOWED_TYPES[extension]:
        # An empty content type from a browser is tolerated; a wrong one is not.
        raise FileValidationError(
            f"The file says it is '{declared}' but has a '{extension}' extension. "
            "Re-save it and try again."
        )

    if size_bytes <= 0:
        raise FileValidationError("That file is empty.")
    if size_bytes > settings.max_upload_bytes:
        raise FileValidationError(
            f"That file is {size_bytes / 1_048_576:.1f} MB. "
            f"The limit is {settings.max_upload_mb} MB."
        )
    return safe_name, extension


def verify_magic(extension: str, data: bytes) -> None:
    """Check the payload's leading bytes against the expected signature.

    Raises:
        FileValidationError: The content does not match its extension.
    """
    expected = _MAGIC.get(extension)
    if not expected:
        return
    if not any(data.startswith(prefix) for prefix in expected):
        raise FileValidationError(
            f"That doesn't look like a real {extension} file -- its contents "
            "don't match its extension."
        )


class FileService:
    """Handles the full upload lifecycle."""

    def __init__(self, storage: Optional[LocalStorage] = None) -> None:
        """Create the service with the given storage backend."""
        self._storage = storage or get_storage()

    async def count_for_session(self, db: AsyncSession, session_id: str) -> int:
        """Return how many files the session already holds."""
        statement = select(func.count()).select_from(UploadedFile).where(
            UploadedFile.session_id == session_id
        )
        return int((await db.execute(statement)).scalar_one())

    async def save_upload(
        self,
        db: AsyncSession,
        *,
        session_id: str,
        original_name: str,
        mime: str,
        data: bytes,
    ) -> StoredUpload:
        """Validate, store, extract and persist one upload.

        Args:
            db: Active database session.
            session_id: Owning chat session.
            original_name: Client-supplied filename.
            mime: Client-supplied content type.
            data: The file bytes.

        Returns:
            A :class:`StoredUpload`.

        Raises:
            FileValidationError: The upload was rejected.
            StorageError: The file could not be written.
        """
        safe_name, extension = validate_upload(original_name, mime, len(data))
        verify_magic(extension, data)

        existing = await self.count_for_session(db, session_id)
        if existing >= settings.max_files_per_session:
            raise FileValidationError(
                f"This chat already has {existing} files "
                f"(limit {settings.max_files_per_session}). Delete one first."
            )

        stored_name = build_stored_name(safe_name)
        is_image = extension in IMAGE_EXTENSIONS
        declared_mime = (mime or "").split(";")[0].strip().lower() or ALLOWED_TYPES[extension][0]

        record = UploadedFile(
            session_id=session_id,
            original_name=safe_name,
            stored_name=stored_name,
            mime=declared_mime,
            size_bytes=len(data),
            status=FileStatus.PENDING,
        )
        db.add(record)
        await db.flush()

        try:
            path = await self._storage.write(stored_name, data)
        except StorageError:
            record.status = FileStatus.FAILED
            record.error = "Could not write the file to storage."
            await db.flush()
            raise

        chunk_count = 0
        if is_image:
            record.status = FileStatus.READY
            record.extracted_chars = 0
        else:
            record.status = FileStatus.EXTRACTING
            await db.flush()
            try:
                result = await extract(Path(path), declared_mime)
            except ExtractionError as exc:
                logger.warning("Extraction failed for %s: %s", safe_name, exc)
                record.status = FileStatus.FAILED
                record.error = str(exc)[:512]
                await db.flush()
                return StoredUpload(file=record, chunk_count=0, is_image=False)

            for chunk in result.chunks:
                db.add(
                    FileChunk(
                        file_id=record.id,
                        chunk_index=chunk.index,
                        content=chunk.content,
                        page=chunk.page,
                    )
                )
            chunk_count = len(result.chunks)
            record.extracted_chars = result.char_count
            record.status = FileStatus.READY if chunk_count else FileStatus.FAILED
            if not chunk_count:
                record.error = "No readable text was found in this file."

        await db.flush()
        logger.info(
            "Upload %s stored for session %s (%d chunk(s), status=%s).",
            safe_name, session_id, chunk_count, record.status.value,
        )
        return StoredUpload(file=record, chunk_count=chunk_count, is_image=is_image)

    async def get(self, db: AsyncSession, file_id: str) -> Optional[UploadedFile]:
        """Fetch one file record by id."""
        return await db.get(UploadedFile, file_id)

    async def list_for_session(
        self, db: AsyncSession, session_id: str
    ) -> Sequence[UploadedFile]:
        """List a session's uploads, newest first."""
        statement = (
            select(UploadedFile)
            .where(UploadedFile.session_id == session_id)
            .order_by(UploadedFile.created_at.desc())
        )
        return (await db.execute(statement)).scalars().all()

    async def delete(self, db: AsyncSession, file_id: str) -> bool:
        """Delete a file record, its chunks and the file on disk.

        Returns:
            True when something was deleted.
        """
        record = await db.get(UploadedFile, file_id)
        if record is None:
            return False
        stored_name = record.stored_name
        try:
            await db.delete(record)
            await db.flush()
        except SQLAlchemyError:
            logger.exception("Could not delete file record %s.", file_id)
            raise
        try:
            await self._storage.delete(stored_name)
        except StorageError as exc:
            # The row is gone; a leftover blob is a housekeeping problem, not a
            # request failure.
            logger.warning("Orphaned blob %s: %s", stored_name, exc)
        return True

    async def load_image_bytes(
        self, db: AsyncSession, file_ids: Sequence[str]
    ) -> list[tuple[UploadedFile, bytes]]:
        """Load the bytes of image attachments for a turn.

        Non-image files and unreadable blobs are skipped with a warning rather
        than failing the whole turn.
        """
        loaded: list[tuple[UploadedFile, bytes]] = []
        for file_id in file_ids:
            record = await db.get(UploadedFile, file_id)
            if record is None or not record.mime.startswith("image/"):
                continue
            try:
                loaded.append((record, await self._storage.read(record.stored_name)))
            except StorageError as exc:
                logger.warning("Skipping unreadable image %s: %s", file_id, exc)
        return loaded


_service: Optional[FileService] = None


def get_file_service() -> FileService:
    """Return the process-wide :class:`FileService`."""
    global _service
    if _service is None:
        _service = FileService()
    return _service
