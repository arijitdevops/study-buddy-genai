"""SQLAlchemy 2.0 ORM models for Study Buddy.

The schema is deliberately small but complete: students, chat sessions,
messages, uploaded files with their extracted chunks, and a guardrail audit
trail. The audit trail is what makes the guardrail layer inspectable -- every
verdict a guard produces is persisted alongside the turn that triggered it.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


#: LONGTEXT on MySQL (messages can exceed TEXT's 64 KB), plain TEXT elsewhere.
LongText = Text().with_variant(LONGTEXT(), "mysql")


def new_uuid() -> str:
    """Return a new 32-character hex UUID used as a primary key."""
    return uuid.uuid4().hex


class MessageRole(str, enum.Enum):
    """Who produced a message."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class FileStatus(str, enum.Enum):
    """Lifecycle of an uploaded file."""

    PENDING = "pending"
    EXTRACTING = "extracting"
    READY = "ready"
    FAILED = "failed"


class GuardVerdict(str, enum.Enum):
    """Outcome of a guardrail evaluation."""

    ALLOW = "allow"
    SOFT_BLOCK = "soft_block"
    HARD_BLOCK = "hard_block"


class Student(Base):
    """A learner using the assistant.

    No email, no password: the sample app identifies a student by an opaque id
    stored in the browser. Keeping personal data out of the schema is itself a
    guardrail.
    """

    __tablename__ = "students"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False, default="Student")
    grade: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    sessions: Mapped[list["ChatSession"]] = relationship(
        back_populates="student",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return f"<Student id={self.id!r} grade={self.grade}>"


class ChatSession(Base, TimestampMixin):
    """A named conversation thread owned by a student."""

    __tablename__ = "chat_sessions"
    __table_args__ = (Index("ix_chat_sessions_student_updated", "student_id", "updated_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    student_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("students.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(160), nullable=False, default="New chat")
    subject: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    student: Mapped["Student"] = relationship(back_populates="sessions", lazy="joined")
    messages: Mapped[list["Message"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
        lazy="selectin",
    )
    files: Mapped[list["UploadedFile"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ChatSession id={self.id!r} title={self.title!r}>"


class Message(Base):
    """A single turn in a conversation.

    ``content`` is stored **after** PII redaction -- see
    :mod:`app.guardrails.pii`.
    """

    __tablename__ = "messages"
    __table_args__ = (Index("ix_messages_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(
        SAEnum(MessageRole, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(LongText, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message_metadata: Mapped[Optional[dict[str, Any]]] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped["ChatSession"] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Message id={self.id!r} role={self.role.value}>"


class UploadedFile(Base):
    """A document or image attached to a session."""

    __tablename__ = "uploaded_files"
    __table_args__ = (Index("ix_uploaded_files_session", "session_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    mime: Mapped[str] = mapped_column(String(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    extracted_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[FileStatus] = mapped_column(
        SAEnum(FileStatus, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=FileStatus.PENDING,
    )
    error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    session: Mapped["ChatSession"] = relationship(back_populates="files")
    chunks: Mapped[list["FileChunk"]] = relationship(
        back_populates="file",
        cascade="all, delete-orphan",
        order_by="FileChunk.chunk_index",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<UploadedFile id={self.id!r} name={self.original_name!r}>"


class FileChunk(Base):
    """A retrievable slice of an uploaded document's extracted text."""

    __tablename__ = "file_chunks"
    __table_args__ = (Index("ix_file_chunks_file_index", "file_id", "chunk_index"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    file_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("uploaded_files.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    file: Mapped["UploadedFile"] = relationship(back_populates="chunks")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<FileChunk file={self.file_id!r} idx={self.chunk_index}>"


class GuardrailEvent(Base):
    """Audit record for one guardrail decision.

    Every guard writes a row here, including ``allow`` verdicts, so that the
    behaviour of the safety layer can be reviewed after the fact.
    """

    __tablename__ = "guardrail_events"
    __table_args__ = (Index("ix_guardrail_events_session_created", "session_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
    session_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=True
    )
    message_id: Mapped[Optional[str]] = mapped_column(
        String(32), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    guard: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[GuardVerdict] = mapped_column(
        SAEnum(GuardVerdict, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<GuardrailEvent guard={self.guard!r} verdict={self.verdict.value}>"
