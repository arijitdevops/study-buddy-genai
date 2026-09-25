"""Concept-explanation node (also handles smalltalk).

This node does not call the model. It assembles the task instruction that the
compose node streams from, which keeps every turn to a single streamed
generation.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agents.prompts.templates import EXPLAIN_PROMPT, SMALLTALK_PROMPT
from app.agents.state import AgentState

logger = logging.getLogger(__name__)


async def explain_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Build the explanation prompt for the compose node.

    Args:
        state: Current agent state.
        config: LangGraph config (unused; kept for node-signature uniformity).

    Returns:
        A partial state update setting ``compose_prompt``.
    """
    question = state.get("effective_message") or state.get("student_message") or ""
    grade = int(state.get("grade", 8))

    if state.get("intent") == "smalltalk":
        logger.debug("Explain node handling smalltalk.")
        return {"compose_prompt": SMALLTALK_PROMPT.format(message=question, grade=grade)}

    return {"compose_prompt": EXPLAIN_PROMPT.format(question=question, grade=grade)}
