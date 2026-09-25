"""Router classification, with a stubbed model and the heuristic fallback."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.router import classify, heuristic_intent, route_from_router
from app.agents.state import AgentState, initial_state
from tests.conftest import StubGeminiService


def _state(message: str, **kwargs: Any) -> AgentState:
    state = initial_state(
        session_id="s1",
        message=message,
        grade=kwargs.pop("grade", 8),
        subject=kwargs.pop("subject", None),
        attachments=kwargs.pop("attachments", None),
    )
    state["effective_message"] = message
    state.update(kwargs)  # type: ignore[typeddict-item]
    return state


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("hi", "smalltalk"),
        ("thanks!", "smalltalk"),
        ("Give me a quiz on the periodic table", "quiz"),
        ("make me flashcards for French verbs", "quiz"),
        ("What is the latest news about the Mars rover?", "search"),
        ("Solve 3x + 5 = 20 step by step", "solve"),
        ("What is photosynthesis?", "explain"),
    ],
)
def test_heuristic_intents(message: str, expected: str) -> None:
    assert heuristic_intent(_state(message)) == expected


def test_heuristic_prefers_doc_qa_when_files_are_attached() -> None:
    state = _state(
        "summarise this chapter",
        attachments=[{"file_id": "f1", "original_name": "notes.pdf",
                      "mime": "application/pdf", "is_image": False}],
    )
    assert heuristic_intent(state) == "doc_qa"


async def test_model_router_is_used_when_available(node_config: dict[str, Any]) -> None:
    stub: StubGeminiService = node_config["configurable"]["deps"].gemini
    stub._json = [{"intent": "solve", "reasoning": "numeric exercise"}]  # noqa: SLF001
    update = await classify(_state("A train travels 60 km in 45 minutes"), node_config)
    assert update["intent"] == "solve"
    assert stub.json_calls


async def test_router_falls_back_when_model_unavailable(node_config: dict[str, Any]) -> None:
    node_config["configurable"]["deps"].gemini.available = False
    update = await classify(_state("Give me a quiz on algebra"), node_config)
    assert update["intent"] == "quiz"
    assert "heuristic" in update["router_reasoning"]


async def test_unknown_intent_falls_back_to_heuristics(node_config: dict[str, Any]) -> None:
    stub: StubGeminiService = node_config["configurable"]["deps"].gemini
    stub._json = [{"intent": "teleport", "reasoning": "nonsense"}]  # noqa: SLF001
    update = await classify(_state("Explain gravity"), node_config)
    assert update["intent"] == "explain"


async def test_search_intent_downgrades_without_a_provider(node_config: dict[str, Any]) -> None:
    stub: StubGeminiService = node_config["configurable"]["deps"].gemini
    stub._json = [{"intent": "search", "reasoning": "needs current info"}]  # noqa: SLF001
    # deps.web_search is None in the fixture, so search cannot actually run.
    update = await classify(_state("Who won the match yesterday?"), node_config)
    assert update["intent"] == "explain"


@pytest.mark.parametrize(
    ("intent", "node"),
    [
        ("explain", "explain"),
        ("solve", "solve"),
        ("doc_qa", "doc_qa"),
        ("quiz", "quiz"),
        ("search", "web_search"),
        ("smalltalk", "explain"),
    ],
)
def test_conditional_edge_mapping(intent: str, node: str) -> None:
    state = _state("x")
    state["intent"] = intent  # type: ignore[typeddict-item]
    assert route_from_router(state) == node
