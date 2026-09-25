"""Chat session CRUD and message history."""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import ChatSession, Message, Student, UploadedFile
from app.deps import CurrentSession, DbSession
from app.schemas.chat import MessageList, MessageRead
from app.schemas.common import SimpleMessage
from app.schemas.session import SessionCreate, SessionList, SessionRead, SessionUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions", tags=["sessions"])


async def _to_read(db: DbSession, session: ChatSession) -> SessionRead:
    """Assemble a :class:`SessionRead` including counts."""
    message_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Message).where(
                    Message.session_id == session.id
                )
            )
        ).scalar_one()
    )
    file_count = int(
        (
            await db.execute(
                select(func.count()).select_from(UploadedFile).where(
                    UploadedFile.session_id == session.id
                )
            )
        ).scalar_one()
    )
    grade = session.student.grade if session.student is not None else 8
    return SessionRead(
        id=session.id,
        student_id=session.student_id,
        title=session.title,
        subject=session.subject,
        grade=grade,
        message_count=message_count,
        file_count=file_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.post(
    "",
    response_model=SessionRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a chat session",
)
async def create_session(payload: SessionCreate, db: DbSession) -> SessionRead:
    """Create a session, creating the student record when needed.

    Args:
        payload: Title, subject, grade and optional existing student id.
        db: Active database session.

    Returns:
        The created session.

    Raises:
        HTTPException: 404 when ``student_id`` refers to an unknown student;
            503 when the database is unreachable.
    """
    try:
        if payload.student_id:
            student: Optional[Student] = await db.get(Student, payload.student_id)
            if student is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No student with id {payload.student_id!r}.",
                )
            student.grade = payload.grade
        else:
            student = Student(display_name=payload.display_name, grade=payload.grade)
            db.add(student)
            await db.flush()

        session = ChatSession(
            student_id=student.id, title=payload.title, subject=payload.subject
        )
        db.add(session)
        await db.flush()
        await db.refresh(session)
    except SQLAlchemyError as exc:
        logger.exception("Could not create chat session.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable; the session was not created.",
        ) from exc

    logger.info("Created session %s for student %s.", session.id, student.id)
    return await _to_read(db, session)


@router.get("", response_model=SessionList, summary="List chat sessions")
async def list_sessions(
    db: DbSession,
    student_id: Annotated[Optional[str], Query(max_length=32)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SessionList:
    """List sessions, most recently updated first."""
    statement = select(ChatSession).order_by(ChatSession.updated_at.desc())
    count_statement = select(func.count()).select_from(ChatSession)
    if student_id:
        statement = statement.where(ChatSession.student_id == student_id)
        count_statement = count_statement.where(ChatSession.student_id == student_id)

    try:
        rows = (await db.execute(statement.limit(limit).offset(offset))).scalars().all()
        total = int((await db.execute(count_statement)).scalar_one())
    except SQLAlchemyError as exc:
        logger.exception("Could not list chat sessions.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable.",
        ) from exc

    return SessionList(items=[await _to_read(db, row) for row in rows], total=total)


@router.get("/{session_id}", response_model=SessionRead, summary="Get one session")
async def get_session(session: CurrentSession, db: DbSession) -> SessionRead:
    """Return a single session with its counts."""
    return await _to_read(db, session)


@router.patch("/{session_id}", response_model=SessionRead, summary="Rename or retag a session")
async def update_session(
    payload: SessionUpdate, session: CurrentSession, db: DbSession
) -> SessionRead:
    """Update the title, subject and/or the student's grade."""
    if payload.title is not None:
        cleaned = payload.title.strip()
        if not cleaned:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Title cannot be blank.",
            )
        session.title = cleaned
    if payload.subject is not None:
        session.subject = payload.subject.strip() or None
    if payload.grade is not None and session.student is not None:
        session.student.grade = payload.grade

    try:
        await db.flush()
        await db.refresh(session)
    except SQLAlchemyError as exc:
        logger.exception("Could not update session %s.", session.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable; nothing was changed.",
        ) from exc
    return await _to_read(db, session)


@router.delete("/{session_id}", response_model=SimpleMessage, summary="Delete a session")
async def delete_session(session: CurrentSession, db: DbSession) -> SimpleMessage:
    """Delete a session and everything cascading from it."""
    session_id = session.id
    try:
        await db.delete(session)
        await db.flush()
    except SQLAlchemyError as exc:
        logger.exception("Could not delete session %s.", session_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable; nothing was deleted.",
        ) from exc
    logger.info("Deleted session %s.", session_id)
    return SimpleMessage(message=f"Session {session_id} deleted.")


@router.get(
    "/{session_id}/messages",
    response_model=MessageList,
    summary="List a session's messages",
)
async def list_messages(
    session: CurrentSession,
    db: DbSession,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MessageList:
    """Return the conversation history in chronological order."""
    try:
        rows = (
            (
                await db.execute(
                    select(Message)
                    .where(Message.session_id == session.id)
                    .order_by(Message.created_at.asc())
                    .limit(limit)
                    .offset(offset)
                )
            )
            .scalars()
            .all()
        )
        total = int(
            (
                await db.execute(
                    select(func.count()).select_from(Message).where(
                        Message.session_id == session.id
                    )
                )
            ).scalar_one()
        )
    except SQLAlchemyError as exc:
        logger.exception("Could not list messages for session %s.", session.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable.",
        ) from exc

    return MessageList(
        items=[
            MessageRead(
                id=row.id,
                session_id=row.session_id,
                role=row.role.value,
                content=row.content,
                token_count=row.token_count,
                message_metadata=row.message_metadata,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=total,
    )
