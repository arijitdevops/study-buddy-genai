"""Turn classification.

The router decides which specialist node handles a turn. It asks Gemini for a
structured verdict, but every branch is also reachable deterministically: when
the model is unavailable the heuristic fallback keeps the agent working, which
matters because the router sits on the critical path of every request.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agents.prompts.templates import ROUTER_PROMPT, ROUTER_SCHEMA
from app.agents.state import AgentDeps, AgentState, Intent, get_deps
from app.services.gemini import GeminiError

logger = logging.getLogger(__name__)

VALID_INTENTS: frozenset[str] = frozenset(
    {"explain", "solve", "doc_qa", "quiz", "search", "smalltalk"}
)

_QUIZ_RE = re.compile(
    r"\b(?:quiz|practice (?:questions|problems)|test me|flash ?cards?|mcqs?|revision questions)\b",
    re.I,
)
_SEARCH_RE = re.compile(
    r"\b(?:latest|current|today|todays|this (?:week|month|year)|recent|news|"
    r"who is the (?:current|present)|as of \d{4}|price of|live score)\b",
    re.I,
)
_SOLVE_RE = re.compile(
    r"\b(?:solve|calculate|evaluate|simplify|find the value|prove|derive|"
    r"how do i (?:solve|work out)|step by step|show the working)\b",
    re.I,
)
_DOC_RE = re.compile(
    r"\b(?:this (?:file|document|pdf|chapter|notes?)|the (?:attached|uploaded)|"
    r"my notes|in the (?:document|pdf|file))\b",
    re.I,
)
_SMALLTALK_RE = re.compile(
    r"^\s*(?:hi|hey|hello|yo|thanks|thank you|ok(?:ay)?|cool|bye|good (?:morning|evening|night))"
    r"[\s!.?]*$",
    re.I,
)
_MATH_EXPR_RE = re.compile(r"\d\s*[-+*/^]\s*\d|\b(?:x|y)\s*=|\\frac|\bsolve for\b")


def heuristic_intent(state: AgentState) -> Intent:
    """Classify a turn without calling a model.

    Used as the fallback whenever the Gemini router is unavailable, and as the
    tie-breaker for obviously-mechanical turns.
    """
    message = state.get("effective_message") or state.get("student_message") or ""
    has_files = bool(state.get("attachments"))

    if _SMALLTALK_RE.match(message):
        return "smalltalk"
    if _QUIZ_RE.search(message):
        return "quiz"
    if has_files and (_DOC_RE.search(message) or len(message.split()) <= 12):
        return "doc_qa"
    if _DOC_RE.search(message) and has_files:
        return "doc_qa"
    if _SEARCH_RE.search(message):
        return "search"
    if _SOLVE_RE.search(message) or _MATH_EXPR_RE.search(message):
        return "solve"
    return "explain"


async def classify(state: AgentState, config: RunnableConfig) -> dict[str, Any]:
    """LangGraph node: set ``intent`` (and ``search_query`` when relevant).

    Args:
        state: Current agent state.
        config: LangGraph config carrying :class:`AgentDeps`.

    Returns:
        A partial state update.
    """
    deps: AgentDeps = get_deps(config)
    message = state.get("effective_message") or state.get("student_message") or ""
    fallback = heuristic_intent(state)

    if deps.gemini is None or not getattr(deps.gemini, "available", False):
        logger.debug("Router falling back to heuristics (Gemini unavailable).")
        return {"intent": fallback, "router_reasoning": "heuristic (model unavailable)"}

    search_available = bool(deps.web_search is not None and getattr(deps.web_search, "available", False))
    prompt = ROUTER_PROMPT.format(
        message=message.strip(),
        has_files="yes" if state.get("attachments") else "no",
        grade=state.get("grade", 8),
        subject=state.get("subject") or "not set",
        search_available="yes" if search_available else "no",
    )

    try:
        payload = await deps.gemini.generate_json(prompt, ROUTER_SCHEMA, temperature=0.0)
    except GeminiError as exc:
        logger.warning("Router model call failed (%s); using heuristics.", exc.__class__.__name__)
        return {"intent": fallback, "router_reasoning": f"heuristic ({exc.__class__.__name__})"}

    intent_raw = str(payload.get("intent", "")).strip().lower()
    intent: Intent = intent_raw if intent_raw in VALID_INTENTS else fallback  # type: ignore[assignment]

    # A "search" verdict is downgraded when search cannot actually run, so the
    # agent answers from model knowledge instead of promising a search.
    if intent == "search" and not search_available:
        logger.info("Search intent downgraded to explain: no search provider configured.")
        intent = "explain"

    update: dict[str, Any] = {
        "intent": intent,
        "router_reasoning": str(payload.get("reasoning", ""))[:300],
    }
    search_query = str(payload.get("search_query", "")).strip()
    if search_query:
        update["search_query"] = search_query
    subject = str(payload.get("subject", "")).strip()
    if subject and not state.get("subject"):
        update["subject"] = subject

    logger.info("Router intent=%s", intent)
    return update


def route_from_router(state: AgentState) -> str:
    """Conditional edge: map ``state['intent']`` to the next node name."""
    intent = state.get("intent", "explain")
    mapping: dict[str, str] = {
        "explain": "explain",
        "solve": "solve",
        "doc_qa": "doc_qa",
        "quiz": "quiz",
        "search": "web_search",
        "smalltalk": "explain",
    }
    return mapping.get(intent, "explain")
