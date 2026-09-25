"""File upload, inspection and deletion."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.config import settings
from app.db.models import ChatSession, UploadedFile
from app.deps import DbSession, Files
from app.schemas.common import SimpleMessage
from app.schemas.file import FileRead, FileUploadResponse
from app.services.file_service import FileValidationError, IMAGE_EXTENSIONS
from app.services.storage import StorageError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])


def _to_read(record: UploadedFile, chunk_count: int) -> FileRead:
    """Convert an ORM row into the API model."""
    return FileRead(
        id=record.id,
        session_id=record.session_id,
        original_name=record.original_name,
        mime=record.mime,
        size_bytes=record.size_bytes,
        extracted_chars=record.extracted_chars,
        status=record.status.value,
        error=record.error,
        chunk_count=chunk_count,
        is_image=record.mime.startswith("image/"),
        created_at=record.created_at,
    )


@router.post(
    "",
    response_model=FileUploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document or image",
)
async def upload_file(
    db: DbSession,
    files: Files,
    session_id: Annotated[str, Form(max_length=32)],
    upload: Annotated[UploadFile, File(alias="file")],
) -> FileUploadResponse:
    """Accept a multipart upload, extract its text and store the chunks.

    Accepted types: PDF, DOCX, TXT, MD, PNG, JPG, WEBP. Images are stored for
    the vision model and are not text-extracted.

    Raises:
        HTTPException: 413 when the file is too large, 415/422 when it is
            rejected by the allowlist, 500 when storage fails.
    """
    if await db.get(ChatSession, session_id) is None:
        await upload.close()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No chat session with id {session_id!r}.",
        )

    try:
        data = await upload.read()
    except Exception as exc:  # noqa: BLE001 - client disconnects land here
        logger.warning("Could not read uploaded file: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The upload was interrupted. Please try again.",
        ) from exc
    finally:
        await upload.close()

    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"That file is {len(data) / 1_048_576:.1f} MB. "
                f"The limit is {settings.max_upload_mb} MB."
            ),
        )

    try:
        stored = await files.save_upload(
            db,
            session_id=session_id,
            original_name=upload.filename or "upload",
            mime=upload.content_type or "",
            data=data,
        )
    except FileValidationError as exc:
        logger.info("Rejected upload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except StorageError as exc:
        logger.exception("Storage failure during upload.")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="The file could not be saved on the server.",
        ) from exc

    record = stored.file
    if record.status.value == "failed":
        message = record.error or "The file was stored but no text could be read from it."
    elif stored.is_image:
        message = f"{record.original_name} is ready. I'll look at it with your next message."
    else:
        message = (
            f"{record.original_name} is ready: {stored.chunk_count} passage(s) indexed. "
            "Ask me anything about it."
        )

    return FileUploadResponse(file=_to_read(record, stored.chunk_count), message=message)


@router.get("/{file_id}", response_model=FileRead, summary="Get an uploaded file's status")
async def get_file(
    file_id: str, db: DbSession, files: Files
) -> FileRead:
    """Return one file's metadata.

    Raises:
        HTTPException: 404 when the file does not exist.
    """
    record = await files.get(db, file_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No file with id {file_id!r}."
        )
    return _to_read(record, len(record.chunks))


@router.delete("/{file_id}", response_model=SimpleMessage, summary="Delete an uploaded file")
async def delete_file(file_id: str, db: DbSession, files: Files) -> SimpleMessage:
    """Delete a file, its chunks and its blob.

    Raises:
        HTTPException: 404 when the file does not exist.
    """
    deleted = await files.delete(db, file_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"No file with id {file_id!r}."
        )
    logger.info("Deleted file %s.", file_id)
    return SimpleMessage(message=f"File {file_id} deleted.")


__all__ = ["router", "IMAGE_EXTENSIONS"]
