"""The LangGraph agent: node wiring, checkpointing and the streaming driver.

Flow::

    input_guard ──(hard block)──> refuse ──> END
         │
         └──(allow / redirect)──> router ──┬──> explain ──┐
                                           ├──> solve ────┤
                                           ├──> doc_qa ───┼──> compose ──> output_guard ──> END
                                           ├──> quiz ─────┤
                                           └──> web_search ┘

``compose`` is the only node that streams; everything before it prepares the
prompt and the context, everything after it inspects the finished answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from langchain_core.runnables import RunnableConfig

from app.agents.nodes.compose import compose_node
from app.agents.nodes.doc_qa import doc_qa_node
from app.agents.nodes.explain import explain_node
from app.agents.nodes.quiz import quiz_node
from app.agents.nodes.refuse import refuse_node
from app.agents.nodes.search import web_search_node
from app.agents.nodes.solve import solve_node
from app.agents.router import classify, route_from_router
from app.agents.state import (
    AgentDeps,
    AgentState,
    ChatTurn,
    GuardrailRecord,
    add_notice,
    get_deps,
    initial_state,
    record_guardrail,
)
from app.guardrails.input_guard import InputGuard
from app.guardrails.output_guard import OutputGuard
from app.services.gemini import GeminiError, GeminiNotConfiguredError

logger = logging.getLogger(__name__)

#: Sentinel pushed onto the event queue when the graph run finishes.
_STREAM_SENTINEL = object()


# ----------------------------------------------------------------------
# Guard nodes
# ----------------------------------------------------------------------
async def input_guard_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Run the pre-agent guard stack and record every verdict.

    Returns:
        A partial state update. On a hard block, ``blocked`` is set and
        ``draft`` carries the message the refusal node will deliver.
    """
    deps: AgentDeps = get_deps(config)
    guard = InputGuard(gemini=deps.gemini)
    outcome = await guard.run(
        state.get("student_message", ""),
        attachment_count=len(state.get("attachments") or []),
        grade=int(state.get("grade", 8)),
    )

    events = list(state.get("guardrail_events") or [])
    for result in outcome.results:
        events.append(
            GuardrailRecord(
                guard=result.guard,
                verdict=result.verdict.value,
                category=result.category.value,
                detail=result.reason[:1000],
            )
        )

    update: dict[str, Any] = {
        "guardrail_events": events,
        "sanitised_message": outcome.sanitised_text,
        "blocked": outcome.blocked,
    }

    if outcome.blocked:
        logger.info("Input guard hard-blocked a turn: %s", outcome.reason)
        await deps.emit_event(
            "guardrail",
            {
                "guard": "input_guard",
                "verdict": "hard_block",
                "category": outcome.category.value,
                "message": outcome.student_message or "",
            },
        )
        update["draft"] = outcome.student_message or ""
        return update

    if outcome.redirected:
        logger.info("Input guard redirected a turn: %s", outcome.reason)
        notice = outcome.student_message or "I've adjusted how I'm answering this one."
        update["notices"] = add_notice(state, notice)
        await deps.emit_event(
            "guardrail",
            {
                "guard": "input_guard",
                "verdict": "soft_block",
                "category": outcome.category.value,
                "message": notice,
            },
        )
        if outcome.replacement_prompt:
            update["effective_message"] = outcome.replacement_prompt
            return update
        # A soft block with no rewrite is a redirect: answer the safe message.
        update["draft"] = notice
        return update

    update["effective_message"] = state.get("student_message", "")
    return update


async def output_guard_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Run the post-generation checks on ``final_answer``.

    Returns:
        A partial state update; ``final_answer`` may be replaced or annotated.
    """
    deps: AgentDeps = get_deps(config)
    answer = state.get("final_answer") or ""
    if not answer:
        return {}

    allowed_urls = state.get("citation_urls")
    outcome = OutputGuard().run(
        answer, allowed_urls=list(allowed_urls) if allowed_urls is not None else None
    )

    events = list(state.get("guardrail_events") or [])
    for result in outcome.results:
        events.append(
            GuardrailRecord(
                guard=result.guard,
                verdict=result.verdict.value,
                category=result.category.value,
                detail=result.reason[:1000],
            )
        )

    update: dict[str, Any] = {"guardrail_events": events}
    if outcome.modified:
        logger.info("Output guard modified the answer: %s", outcome.reason)
        update["final_answer"] = outcome.text
        await deps.emit_event(
            "guardrail",
            {
                "guard": "output_guard",
                "verdict": outcome.verdict.value,
                "category": outcome.category.value,
                "message": outcome.reason,
                "replacement": outcome.text,
            },
        )
    return update


def route_from_input_guard(state: AgentState) -> str:
    """Conditional edge: short-circuit to the refusal node on a hard block."""
    if state.get("blocked"):
        return "refuse"
    if state.get("draft"):
        # A soft-block redirect already has its answer text.
        return "compose"
    return "router"


# ----------------------------------------------------------------------
# Graph construction
# ----------------------------------------------------------------------
def build_graph(checkpointer: Any | None = None) -> Any:
    """Build and compile the agent graph.

    Args:
        checkpointer: A LangGraph checkpointer. Defaults to an in-process
            :class:`MemorySaver`, which is enough for a single-worker
            deployment; swap in ``AsyncSqliteSaver`` or a Postgres/MySQL saver
            to survive a restart.

    Returns:
        The compiled graph.
    """
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node("input_guard", input_guard_node)
    graph.add_node("router", classify)
    graph.add_node("explain", explain_node)
    graph.add_node("solve", solve_node)
    graph.add_node("doc_qa", doc_qa_node)
    graph.add_node("quiz", quiz_node)
    graph.add_node("web_search", web_search_node)
    graph.add_node("compose", compose_node)
    graph.add_node("output_guard", output_guard_node)
    graph.add_node("refuse", refuse_node)

    graph.set_entry_point("input_guard")
    graph.add_conditional_edges(
        "input_guard",
        route_from_input_guard,
        {"refuse": "refuse", "compose": "compose", "router": "router"},
    )
    graph.add_conditional_edges(
        "router",
        route_from_router,
        {
            "explain": "explain",
            "solve": "solve",
            "doc_qa": "doc_qa",
            "quiz": "quiz",
            "web_search": "web_search",
        },
    )
    for node in ("explain", "solve", "doc_qa", "quiz", "web_search"):
        graph.add_edge(node, "compose")
    graph.add_edge("compose", "output_guard")
    graph.add_edge("output_guard", END)
    graph.add_edge("refuse", END)

    compiled = graph.compile(checkpointer=checkpointer or MemorySaver())
    logger.info("Agent graph compiled.")
    return compiled


_graph: Any = None


def get_graph() -> Any:
    """Return the process-wide compiled graph, building it on first use."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ----------------------------------------------------------------------
# Drivers
# ----------------------------------------------------------------------
async def run_agent(
    *,
    session_id: str,
    message: str,
    grade: int,
    deps: AgentDeps,
    subject: Optional[str] = None,
    attachments: Optional[list[dict[str, Any]]] = None,
    history: Optional[list[ChatTurn]] = None,
) -> AgentState:
    """Run one turn to completion without streaming.

    Args:
        session_id: Chat session id; doubles as the checkpoint thread id.
        message: The student's message.
        grade: School class 1-12.
        deps: Live dependencies for the nodes.
        subject: Optional current subject.
        attachments: Attachment records for this turn.
        history: Prior turns.

    Returns:
        The final :class:`AgentState`.
    """
    state = initial_state(
        session_id=session_id,
        message=message,
        grade=grade,
        subject=subject,
        attachments=attachments or [],  # type: ignore[arg-type]
        history=history or [],
    )
    config = {"configurable": {"deps": deps, "thread_id": session_id}}
    result = await get_graph().ainvoke(state, config=config)
    return result  # type: ignore[return-value]


async def stream_agent(
    *,
    session_id: str,
    message: str,
    grade: int,
    deps: AgentDeps,
    subject: Optional[str] = None,
    attachments: Optional[list[dict[str, Any]]] = None,
    history: Optional[list[ChatTurn]] = None,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Run one turn and yield ``(event_type, payload)`` as it happens.

    The graph is driven in a background task that pushes events onto a queue;
    this generator drains the queue so the HTTP layer can translate each event
    into an SSE frame. The final ``done`` event carries the guard-corrected
    answer and the turn's metadata.

    Yields:
        ``("token" | "tool_call" | "guardrail" | "done" | "error", payload)``.
    """
    queue: asyncio.Queue[Any] = asyncio.Queue()

    async def emit(event_type: str, payload: dict[str, Any]) -> None:
        await queue.put((event_type, payload))

    deps.emit = emit
    final_state: dict[str, Any] = {}

    async def runner() -> None:
        try:
            state = await run_agent(
                session_id=session_id,
                message=message,
                grade=grade,
                deps=deps,
                subject=subject,
                attachments=attachments,
                history=history,
            )
            final_state.update(state)
        except GeminiNotConfiguredError as exc:
            await queue.put(("error", {"message": str(exc), "detail": "GeminiNotConfiguredError"}))
        except GeminiError as exc:
            logger.warning("Gemini failure for session %s: %s", session_id, exc)
            await queue.put(("error", {
                "message": "I couldn't reach the language model just now. Please try again in a moment.",
                "detail": exc.__class__.__name__,
            }))
        except Exception as exc:  # noqa: BLE001 - reported to the client
            logger.exception("Agent run failed for session %s.", session_id)
            await queue.put(("error", {"message": "The assistant hit an internal error.",
                                       "detail": exc.__class__.__name__}))
        finally:
            await queue.put(_STREAM_SENTINEL)

    task = asyncio.create_task(runner())
    try:
        while True:
            item = await queue.get()
            if item is _STREAM_SENTINEL:
                break
            yield item  # type: ignore[misc]
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    if final_state:
        yield (
            "done",
            {
                "answer": final_state.get("final_answer", ""),
                "intent": final_state.get("intent"),
                "blocked": bool(final_state.get("blocked")),
                "notices": final_state.get("notices") or [],
                "tool_results": final_state.get("tool_results") or [],
                "guardrail_events": final_state.get("guardrail_events") or [],
                "quiz": final_state.get("quiz"),
                "citations": final_state.get("citation_urls") or [],
            },
        )


def format_sse(event_type: str, payload: dict[str, Any]) -> str:
    """Format one event as an SSE frame.

    Args:
        event_type: One of ``token``, ``tool_call``, ``guardrail``, ``done``,
            ``error``.
        payload: JSON-serialisable event body.

    Returns:
        The wire representation, terminated by a blank line.
    """
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {body}\n\n"
