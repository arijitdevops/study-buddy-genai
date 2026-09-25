"""Typed state and runtime dependencies for the LangGraph agent.

``AgentState`` is the channel dictionary LangGraph threads through every node.
It holds only JSON-serialisable values so that checkpointing works; live
objects (database session, Gemini client, tools, the SSE emitter) travel
separately in :class:`AgentDeps`, handed to the graph through
``config["configurable"]["deps"]``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional, TypedDict

logger = logging.getLogger(__name__)

Intent = Literal["explain", "solve", "doc_qa", "quiz", "search", "smalltalk", "refuse"]

#: Event types emitted over SSE. Mirrored by ``frontend/src/types.ts``.
EventType = Literal["token", "tool_call", "guardrail", "done", "error"]


class ChatTurn(TypedDict):
    """One stored conversation turn, as handed to the model."""

    role: Literal["user", "model"]
    text: str


class AttachmentRef(TypedDict, total=False):
    """A file attached to the current turn."""

    file_id: str
    original_name: str
    mime: str
    is_image: bool
    #: Raw bytes, present only for images on the current turn. The in-memory
    #: checkpointer handles bytes; a JSON-backed checkpointer would need this
    #: stripped before persistence.
    data: bytes


class ToolRecord(TypedDict, total=False):
    """A record of one tool invocation, surfaced to the UI and the audit log."""

    tool: str
    query: str
    ok: bool
    detail: str
    urls: list[str]


class GuardrailRecord(TypedDict, total=False):
    """A guardrail decision, persisted to ``guardrail_events``."""

    guard: str
    verdict: str
    category: str
    detail: str


class AgentState(TypedDict, total=False):
    """The LangGraph channel state.

    Every key is optional so that nodes can return partial updates, which is
    how LangGraph merges node output into the running state.
    """

    # --- Turn input ---
    session_id: str
    student_message: str
    #: PII-redacted copy, safe to log and persist.
    sanitised_message: str
    #: The message actually sent to the model. Differs from
    #: ``student_message`` when a guard rewrote the turn.
    effective_message: str
    grade: int
    subject: Optional[str]
    attachments: list[AttachmentRef]
    history: list[ChatTurn]

    # --- Routing ---
    intent: Intent
    router_reasoning: str
    search_query: str

    # --- Retrieval / tools ---
    retrieved_context: str
    tool_results: list[ToolRecord]
    citation_urls: list[str]
    #: The task instruction the compose node streams from. Specialist nodes
    #: build it; when ``draft`` is already set, compose streams that instead
    #: and makes no model call.
    compose_prompt: str

    # --- Guardrails ---
    guardrail_events: list[GuardrailRecord]
    #: Short lines shown to the student when a turn was redirected or trimmed.
    notices: list[str]
    blocked: bool

    # --- Output ---
    draft: str
    final_answer: str
    quiz: Optional[dict[str, Any]]
    error: Optional[str]


EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class AgentDeps:
    """Live objects a node needs, passed outside the checkpointed state.

    Attributes:
        gemini: The Gemini service facade.
        db: Async database session for document retrieval.
        web_search: Web search tool.
        wikipedia: Wikipedia tool.
        doc_retrieval: Document retrieval tool.
        emit: Optional async callback used to stream events to the client.
    """

    gemini: Any
    db: Any = None
    web_search: Any = None
    wikipedia: Any = None
    doc_retrieval: Any = None
    emit: Optional[EmitFn] = None

    async def emit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Emit an event if an emitter is attached; never raises."""
        if self.emit is None:
            return
        try:
            await self.emit(event_type, data)
        except Exception:  # noqa: BLE001 - a broken client must not fail the turn
            logger.warning("Event emitter failed for %s event.", event_type, exc_info=True)


def get_deps(config: Optional[dict[str, Any]]) -> AgentDeps:
    """Extract :class:`AgentDeps` from a LangGraph runnable config.

    Args:
        config: The config LangGraph passes to each node.

    Returns:
        The dependencies bundle.

    Raises:
        RuntimeError: The graph was invoked without dependencies.
    """
    deps = (config or {}).get("configurable", {}).get("deps")
    if not isinstance(deps, AgentDeps):
        raise RuntimeError(
            "The agent graph was invoked without AgentDeps. Pass "
            'config={"configurable": {"deps": AgentDeps(...), "thread_id": ...}}.'
        )
    return deps


def initial_state(
    *,
    session_id: str,
    message: str,
    grade: int,
    subject: Optional[str] = None,
    attachments: Optional[list[AttachmentRef]] = None,
    history: Optional[list[ChatTurn]] = None,
) -> AgentState:
    """Build a fresh :class:`AgentState` for one turn."""
    return AgentState(
        session_id=session_id,
        student_message=message,
        sanitised_message=message,
        effective_message=message,
        grade=grade,
        subject=subject,
        attachments=attachments or [],
        history=history or [],
        tool_results=[],
        citation_urls=[],
        guardrail_events=[],
        notices=[],
        blocked=False,
        draft="",
        final_answer="",
        quiz=None,
        error=None,
    )


def record_guardrail(state: AgentState, record: GuardrailRecord) -> list[GuardrailRecord]:
    """Return the guardrail list for ``state`` with ``record`` appended."""
    events = list(state.get("guardrail_events") or [])
    events.append(record)
    return events


def add_notice(state: AgentState, notice: str) -> list[str]:
    """Return the notice list for ``state`` with ``notice`` appended."""
    notices = list(state.get("notices") or [])
    if notice not in notices:
        notices.append(notice)
    return notices
