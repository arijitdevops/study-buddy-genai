"""Composition node: the single streamed generation of a turn.

Two paths:

* ``state["draft"]`` already holds text (a quiz, a refusal) -- it is emitted in
  small slices so the UI behaves identically, with no model call.
* Otherwise the node streams a generation from ``state["compose_prompt"]``
  using the grade-aware system prompt, emitting each delta as it arrives.

The accumulated text lands in ``final_answer`` for the output guard to inspect.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agents.prompts.system import build_system_prompt
from app.agents.state import AgentDeps, AgentState, get_deps
from app.services.gemini import (
    GeminiBlockedError,
    GeminiError,
    GeminiNotConfiguredError,
    InlineImage,
)

logger = logging.getLogger(__name__)

#: Characters per emitted slice when replaying a pre-built draft.
_REPLAY_SLICE = 24
#: Pause between replayed slices, so the UI streams rather than snapping in.
_REPLAY_DELAY_SECONDS = 0.01

_NO_KEY_MESSAGE = (
    "I can't answer right now because this deployment has no Gemini API key "
    "configured.\n\n"
    "If you're running Study Buddy yourself: copy `backend/.env.example` to "
    "`backend/.env`, add `GEMINI_API_KEY`, and restart the backend. "
    "`GET /api/health` will tell you what else is missing."
)

_FAILURE_MESSAGE = (
    "I hit a problem reaching my language model and couldn't finish that answer. "
    "Give it another go in a moment -- your chat history is saved."
)


async def compose_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Produce and stream the answer.

    Args:
        state: Current agent state.
        config: LangGraph config carrying :class:`AgentDeps`.

    Returns:
        A partial state update with ``final_answer`` (and ``error`` on failure).
    """
    deps: AgentDeps = get_deps(config)

    draft = state.get("draft") or ""
    if draft:
        await _replay(deps, draft)
        return {"final_answer": draft}

    prompt = state.get("compose_prompt") or state.get("effective_message") or ""
    if not prompt:
        logger.error("Compose node reached with nothing to say.")
        return {"final_answer": _FAILURE_MESSAGE, "error": "empty compose prompt"}

    system_prompt = build_system_prompt(
        grade=int(state.get("grade", 8)), subject=state.get("subject")
    )
    images = _collect_images(state)

    collected: list[str] = []
    try:
        async for delta in deps.gemini.stream(
            prompt,
            system_instruction=system_prompt,
            history=_history_for_model(state),
            images=images or None,
        ):
            collected.append(delta)
            await deps.emit_event("token", {"text": delta})
    except GeminiNotConfiguredError as exc:
        logger.warning("Compose node: %s", exc)
        await _replay(deps, _NO_KEY_MESSAGE)
        return {"final_answer": _NO_KEY_MESSAGE, "error": "gemini_not_configured"}
    except GeminiBlockedError as exc:
        logger.info("Compose node: model safety filter fired (%s).", exc)
        message = (
            "My model's own safety filter stopped that answer. If this is a "
            "curriculum topic, tell me the subject and chapter and I'll approach "
            "it from there."
        )
        await _replay(deps, message)
        return {"final_answer": message, "error": "model_safety_block"}
    except GeminiError as exc:
        logger.exception("Compose node generation failed.")
        partial = "".join(collected).strip()
        if partial:
            # Keep what the student already saw; append an honest tail.
            tail = "\n\n*(I lost my connection mid-answer -- ask me to continue.)*"
            await _replay(deps, tail)
            return {"final_answer": partial + tail, "error": str(exc)}
        await _replay(deps, _FAILURE_MESSAGE)
        return {"final_answer": _FAILURE_MESSAGE, "error": str(exc)}

    answer = "".join(collected).strip()
    if not answer:
        logger.warning("Compose node received an empty stream.")
        await _replay(deps, _FAILURE_MESSAGE)
        return {"final_answer": _FAILURE_MESSAGE, "error": "empty_response"}

    logger.info("Composed answer of %d characters.", len(answer))
    return {"final_answer": answer}


async def _replay(deps: AgentDeps, text: str) -> None:
    """Emit ``text`` as a sequence of token events."""
    for start in range(0, len(text), _REPLAY_SLICE):
        await deps.emit_event("token", {"text": text[start : start + _REPLAY_SLICE]})
        await asyncio.sleep(_REPLAY_DELAY_SECONDS)


def _history_for_model(state: AgentState) -> list[dict[str, str]]:
    """Convert stored history into the shape the Gemini service expects."""
    return [
        {"role": turn.get("role", "user"), "text": turn.get("text", "")}
        for turn in (state.get("history") or [])
        if turn.get("text")
    ]


def _collect_images(state: AgentState) -> list[InlineImage]:
    """Load image attachments for this turn.

    Image bytes are read by the file service before the graph runs and placed
    on the attachment record; anything without bytes is skipped rather than
    triggering disk I/O inside a node.
    """
    images: list[InlineImage] = []
    for attachment in state.get("attachments") or []:
        if not attachment.get("is_image"):
            continue
        data = attachment.get("data")
        mime = attachment.get("mime") or "image/png"
        if isinstance(data, (bytes, bytearray)):
            images.append(InlineImage(data=bytes(data), mime_type=str(mime)))
    return images
