"""Streaming chat endpoint.

The response is a Server-Sent Events stream. Because the body outlives the
handler, this route does **not** use the request-scoped database dependency:
that session would be committed and closed the moment the handler returns a
``StreamingResponse``. Instead the generator opens its own session and owns its
transaction for the whole turn.

Event stream contract::

    event: token
    data: {"text": "Photo"}

    event: tool_call
    data: {"tool": "web_search", "status": "running", "detail": "Searching..."}

    event: guardrail
    data: {"guard": "input_guard", "verdict": "soft_block",
           "category": "academic_integrity", "message": "..."}

    event: done
    data: {"answer": "...", "message_id": "...", "intent": "explain", ...}

    event: error
    data: {"message": "...", "detail": "GeminiTransientError"}

Exactly one terminal event (``done`` or ``error``) is sent per request.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import format_sse, stream_agent
from app.agents.state import AgentDeps, AttachmentRef, ChatTurn
from app.db.models import ChatSession, GuardrailEvent, GuardVerdict, Message, MessageRole
from app.db.session import get_session_factory
from app.deps import Files, Gemini, Retrieval, Search, Wiki
from app.guardrails.pii import scan_and_redact
from app.schemas.chat import ChatRequest
from app.services.gemini import GeminiNotConfiguredError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])

#: How many prior turns to replay to the model.
HISTORY_TURNS = 12
#: Rough characters-per-token estimate, used only for bookkeeping.
CHARS_PER_TOKEN = 4

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    # Stops nginx buffering the stream into one lump.
    "X-Accel-Buffering": "no",
}


def _estimate_tokens(text: str) -> int:
    """Cheap token estimate for storage and cost bookkeeping."""
    return max(1, len(text) // CHARS_PER_TOKEN)


async def _load_history(db: AsyncSession, session_id: str) -> list[ChatTurn]:
    """Load the last :data:`HISTORY_TURNS` turns for the model."""
    rows = (
        (
            await db.execute(
                select(Message)
                .where(
                    Message.session_id == session_id,
                    Message.role.in_([MessageRole.USER, MessageRole.ASSISTANT]),
                )
                .order_by(Message.created_at.desc())
                .limit(HISTORY_TURNS)
            )
        )
        .scalars()
        .all()
    )
    return [
        ChatTurn(role="model" if row.role is MessageRole.ASSISTANT else "user", text=row.content)
        for row in reversed(rows)
    ]


async def _build_attachments(
    db: AsyncSession, files: Files, file_ids: list[str]
) -> list[AttachmentRef]:
    """Turn requested file ids into attachment records, loading image bytes."""
    attachments: list[AttachmentRef] = []
    image_bytes = {
        record.id: data for record, data in await files.load_image_bytes(db, file_ids)
    }
    for file_id in file_ids:
        record = await files.get(db, file_id)
        if record is None:
            logger.info("Ignoring unknown attachment %s.", file_id)
            continue
        attachment = AttachmentRef(
            file_id=record.id,
            original_name=record.original_name,
            mime=record.mime,
            is_image=record.mime.startswith("image/"),
        )
        if record.id in image_bytes:
            attachment["data"] = image_bytes[record.id]
        attachments.append(attachment)
    return attachments


async def _persist_guardrails(
    db: AsyncSession,
    session_id: str,
    message_id: Optional[str],
    events: list[dict[str, Any]],
) -> None:
    """Write the turn's guardrail decisions to the audit table."""
    for event in events:
        try:
            verdict = GuardVerdict(str(event.get("verdict", "allow")))
        except ValueError:
            verdict = GuardVerdict.ALLOW
        db.add(
            GuardrailEvent(
                session_id=session_id,
                message_id=message_id,
                guard=str(event.get("guard", "unknown"))[:64],
                verdict=verdict,
                category=(str(event.get("category")) or None) if event.get("category") else None,
                detail=str(event.get("detail", ""))[:1024] or None,
            )
        )


@router.post(
    "/chat",
    summary="Send a message and stream the reply",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "SSE stream of token / tool_call / guardrail / done / error events.",
        },
        404: {"description": "Unknown session."},
        503: {"description": "GEMINI_API_KEY is not configured, or the database is down."},
    },
)
async def chat(
    payload: ChatRequest,
    request: Request,
    gemini: Gemini,
    files: Files,
    search: Search,
    wiki: Wiki,
    retrieval: Retrieval,
) -> StreamingResponse:
    """Run one turn of the agent and stream the result.

    Args:
        payload: The chat request.
        request: Used to detect client disconnects.
        gemini: Gemini service.
        files: File service, for attachments.
        search: Web search tool.
        wiki: Wikipedia tool.
        retrieval: Document retrieval tool.

    Returns:
        A ``text/event-stream`` response.

    Raises:
        HTTPException: 404 when the session does not exist, 503 when the
            database is unreachable.
    """
    if not gemini.available:
        # Fail fast with a clear, non-streaming error: nothing useful can
        # happen without a model, and the client shows this message verbatim.
        raise GeminiNotConfiguredError(
            "The assistant isn't configured yet: GEMINI_API_KEY is not set on the server. "
            "Copy backend/.env.example to backend/.env, add your key from "
            "https://aistudio.google.com/apikey and restart the backend."
        )

    factory = get_session_factory()

    # Validate the session up front so a bad id is a clean 404, not an SSE
    # error frame the client has to parse.
    try:
        async with factory() as probe:
            session = await probe.get(ChatSession, payload.session_id)
            if session is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No chat session with id {payload.session_id!r}.",
                )
            grade = payload.grade or (session.student.grade if session.student else 8)
            subject = payload.subject or session.subject
    except SQLAlchemyError as exc:
        logger.exception("Database unavailable at the start of a chat turn.")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="The database is unavailable, so I can't save this conversation.",
        ) from exc

    async def event_stream() -> AsyncIterator[str]:
        """Drive the agent and yield SSE frames."""
        async with factory() as db:
            try:
                history = await _load_history(db, payload.session_id)
                attachments = await _build_attachments(db, files, payload.file_ids)

                redacted = scan_and_redact(payload.message)
                user_message = Message(
                    session_id=payload.session_id,
                    role=MessageRole.USER,
                    content=redacted.redacted_text,
                    token_count=_estimate_tokens(payload.message),
                    message_metadata={
                        "attachments": [a.get("file_id") for a in attachments],
                        "pii_redacted": redacted.count,
                    },
                )
                db.add(user_message)
                await db.flush()

                deps = AgentDeps(
                    gemini=gemini,
                    db=db,
                    web_search=search,
                    wikipedia=wiki,
                    doc_retrieval=retrieval,
                )

                final: dict[str, Any] = {}
                failed = False
                async for event_type, event_payload in stream_agent(
                    session_id=payload.session_id,
                    message=payload.message,
                    grade=grade,
                    deps=deps,
                    subject=subject,
                    attachments=attachments,  # type: ignore[arg-type]
                    history=history,
                ):
                    if event_type == "done":
                        final = event_payload
                        continue
                    if event_type == "error":
                        failed = True
                    yield format_sse(event_type, event_payload)
                    if await request.is_disconnected():
                        logger.info("Client disconnected mid-stream; stopping.")
                        break

                answer = str(final.get("answer", ""))
                assistant_message: Optional[Message] = None
                if answer:
                    assistant_message = Message(
                        session_id=payload.session_id,
                        role=MessageRole.ASSISTANT,
                        content=scan_and_redact(answer).redacted_text,
                        token_count=_estimate_tokens(answer),
                        message_metadata={
                            "intent": final.get("intent"),
                            "blocked": final.get("blocked", False),
                            "citations": final.get("citations", []),
                            "tools": [t.get("tool") for t in final.get("tool_results", [])],
                        },
                    )
                    db.add(assistant_message)
                    await db.flush()

                await _persist_guardrails(
                    db,
                    payload.session_id,
                    assistant_message.id if assistant_message else user_message.id,
                    list(final.get("guardrail_events", [])),
                )
                await db.commit()

                if failed:
                    # The error frame already went out: exactly one terminal event.
                    return
                final["message_id"] = assistant_message.id if assistant_message else None
                yield format_sse("done", final)

            except SQLAlchemyError as exc:
                await db.rollback()
                logger.exception("Database error during a chat turn.")
                yield format_sse(
                    "error",
                    {
                        "message": "I couldn't save this conversation, so I stopped.",
                        "detail": exc.__class__.__name__,
                    },
                )
            except Exception as exc:  # noqa: BLE001 - never leak a traceback
                await db.rollback()
                logger.exception("Unhandled error during a chat turn.")
                yield format_sse(
                    "error",
                    {
                        "message": "Something went wrong on my side. Please try again.",
                        "detail": exc.__class__.__name__,
                    },
                )

    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS
    )
