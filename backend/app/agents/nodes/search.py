"""Current-information node.

Tries the web-search tool first and falls back to Wikipedia. Both return
untrusted text, which is wrapped before it enters the prompt, and both
contribute the URL allowlist the output guard uses to verify citations.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agents.prompts.templates import EXPLAIN_PROMPT, SEARCH_PROMPT
from app.agents.state import (
    AgentDeps,
    AgentState,
    GuardrailRecord,
    ToolRecord,
    add_notice,
    get_deps,
    record_guardrail,
)
from app.agents.tools.web_search import WebSearchError, WebSearchNotConfiguredError
from app.agents.tools.wikipedia import WikipediaError

logger = logging.getLogger(__name__)

_INJECTION_NOTICE = (
    "A page in the search results tried to give me instructions. I've treated it "
    "as ordinary text and ignored them."
)


async def web_search_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Gather current information and build the search-grounded prompt.

    Args:
        state: Current agent state.
        config: LangGraph config carrying :class:`AgentDeps`.

    Returns:
        A partial state update.
    """
    deps: AgentDeps = get_deps(config)
    question = state.get("effective_message") or state.get("student_message") or ""
    query = state.get("search_query") or question
    grade = int(state.get("grade", 8))

    records: list[ToolRecord] = list(state.get("tool_results") or [])
    urls: list[str] = list(state.get("citation_urls") or [])
    update: dict[str, Any] = {}
    context: Optional[str] = None
    injection_summary: Optional[str] = None

    # --- Primary: web search -------------------------------------------
    if deps.web_search is not None and getattr(deps.web_search, "available", False):
        await deps.emit_event(
            "tool_call", {"tool": "web_search", "status": "running",
                          "detail": f"Searching the web for “{query}”..."}
        )
        try:
            response = await deps.web_search.search(query)
        except WebSearchNotConfiguredError as exc:
            logger.info("Web search not configured: %s", exc)
            records.append(ToolRecord(tool="web_search", query=query, ok=False,
                                      detail=str(exc), urls=[]))
            update["notices"] = add_notice(state, str(exc))
        except WebSearchError as exc:
            logger.warning("Web search failed: %s", exc)
            records.append(ToolRecord(tool="web_search", query=query, ok=False,
                                      detail=str(exc), urls=[]))
            await deps.emit_event("tool_call", {"tool": "web_search", "status": "error",
                                                "detail": "Search failed; trying Wikipedia."})
        else:
            context = response.as_prompt_block()
            urls.extend(response.urls)
            records.append(
                ToolRecord(tool="web_search", query=query, ok=True,
                           detail=f"{response.provider}: {len(response.results)} result(s)",
                           urls=response.urls)
            )
            if response.injection.flagged:
                injection_summary = response.injection.summary()
            await deps.emit_event(
                "tool_call",
                {"tool": "web_search", "status": "done",
                 "detail": f"Found {len(response.results)} source(s)"},
            )
    else:
        reason = (
            deps.web_search.unavailable_reason
            if deps.web_search is not None
            else "Web search is not wired up in this deployment."
        )
        logger.info("Skipping web search: %s", reason)
        update["notices"] = add_notice(state, reason)

    # --- Fallback: Wikipedia -------------------------------------------
    if context is None and deps.wikipedia is not None:
        await deps.emit_event("tool_call", {"tool": "wikipedia", "status": "running",
                                            "detail": "Checking Wikipedia..."})
        try:
            wiki = await deps.wikipedia.lookup(query)
        except WikipediaError as exc:
            logger.warning("Wikipedia fallback failed: %s", exc)
            records.append(ToolRecord(tool="wikipedia", query=query, ok=False,
                                      detail=str(exc), urls=[]))
            await deps.emit_event("tool_call", {"tool": "wikipedia", "status": "error",
                                                "detail": str(exc)})
        else:
            if wiki.articles:
                context = wiki.as_prompt_block()
                urls.extend(wiki.urls)
                if wiki.injection.flagged:
                    injection_summary = wiki.injection.summary()
            records.append(
                ToolRecord(tool="wikipedia", query=query, ok=bool(wiki.articles),
                           detail=f"{len(wiki.articles)} article(s)", urls=wiki.urls)
            )
            await deps.emit_event(
                "tool_call",
                {"tool": "wikipedia", "status": "done",
                 "detail": f"Read {len(wiki.articles)} Wikipedia article(s)"},
            )

    if injection_summary:
        update["guardrail_events"] = record_guardrail(
            state,
            GuardrailRecord(guard="prompt_injection", verdict="soft_block",
                            category="prompt_injection", detail=injection_summary),
        )
        notices = update.get("notices") or list(state.get("notices") or [])
        if _INJECTION_NOTICE not in notices:
            notices.append(_INJECTION_NOTICE)
        update["notices"] = notices
        await deps.emit_event("guardrail", {"guard": "prompt_injection",
                                            "verdict": "soft_block",
                                            "message": _INJECTION_NOTICE})

    update["tool_results"] = records
    update["citation_urls"] = urls

    if context is None:
        logger.info("No external context available; answering from model knowledge.")
        update["compose_prompt"] = (
            EXPLAIN_PROMPT.format(question=question, grade=grade)
            + "\n\nYou could not look anything up for this answer. Say plainly that "
            "your information may be out of date, and tell the student what to "
            "check for themselves."
        )
        return update

    update["compose_prompt"] = SEARCH_PROMPT.format(
        question=question, grade=grade, context=context
    )
    update["retrieved_context"] = context
    return update
