"""Prompt constants and builders for the agent nodes."""

from app.agents.prompts.system import (
    BASE_SYSTEM_PROMPT,
    REFUSAL_SYSTEM_PROMPT,
    build_system_prompt,
)
from app.agents.prompts.templates import (
    COMPOSE_PROMPT,
    DOC_QA_PROMPT,
    EXPLAIN_PROMPT,
    FLASHCARD_PROMPT,
    FLASHCARD_SCHEMA,
    QUIZ_PROMPT,
    QUIZ_SCHEMA,
    ROUTER_PROMPT,
    ROUTER_SCHEMA,
    SEARCH_PROMPT,
    SMALLTALK_PROMPT,
    SOLVE_PROMPT,
)

__all__ = [
    "BASE_SYSTEM_PROMPT",
    "COMPOSE_PROMPT",
    "DOC_QA_PROMPT",
    "EXPLAIN_PROMPT",
    "FLASHCARD_PROMPT",
    "FLASHCARD_SCHEMA",
    "QUIZ_PROMPT",
    "QUIZ_SCHEMA",
    "REFUSAL_SYSTEM_PROMPT",
    "ROUTER_PROMPT",
    "ROUTER_SCHEMA",
    "SEARCH_PROMPT",
    "SMALLTALK_PROMPT",
    "SOLVE_PROMPT",
    "build_system_prompt",
]
