"""Shared test fixtures.

Nothing here touches the network or a real database. Gemini is replaced with a
stub whose responses each test scripts, so guardrail and routing behaviour is
asserted deterministically.
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, AsyncIterator, Optional, Sequence

import pytest

# Make `app` importable when pytest is run from the repository root.
BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Configure the app for tests *before* anything imports app.config: a throwaway
# SQLite database and upload folder, no Gemini key and no paid search keys.
# Real environment variables win over backend/.env, so a developer's own key
# never leaks into the suite.
_TEST_DIR = Path(tempfile.mkdtemp(prefix="study-buddy-tests-"))
atexit.register(shutil.rmtree, _TEST_DIR, ignore_errors=True)
os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{(_TEST_DIR / 'test.db').as_posix()}",
        "UPLOAD_DIR": str(_TEST_DIR / "uploads"),
        "GEMINI_API_KEY": "",
        "TAVILY_API_KEY": "",
        "SERPER_API_KEY": "",
        "RATE_LIMIT_PER_MINUTE": "1000",
        "LOG_LEVEL": "WARNING",
    }
)

from app.agents.state import AgentDeps  # noqa: E402


class StubGeminiService:
    """A drop-in replacement for ``GeminiService`` with scripted responses.

    Args:
        json_responses: Queue of dicts returned by ``generate_json``.
        text_responses: Queue of strings returned by ``generate``/``stream``.
        available: Whether the stub reports itself as configured.
        raises: When set, every call raises this exception.
    """

    def __init__(
        self,
        *,
        json_responses: Optional[Sequence[dict[str, Any]]] = None,
        text_responses: Optional[Sequence[str]] = None,
        available: bool = True,
        raises: Optional[BaseException] = None,
    ) -> None:
        self._json = list(json_responses or [])
        self._text = list(text_responses or ["stub answer"])
        self.available = available
        self._raises = raises
        self.default_model = "stub-model"
        self.vision_model = "stub-vision"
        self.guard_model = "stub-guard"
        self.json_calls: list[tuple[str, dict[str, Any]]] = []
        self.stream_calls: list[str] = []
        self.stream_kwargs: list[dict[str, Any]] = []

    async def generate_json(
        self, prompt: str, response_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        """Return the next scripted JSON payload."""
        if self._raises is not None:
            raise self._raises
        self.json_calls.append((prompt, response_schema))
        if not self._json:
            return {}
        return self._json.pop(0)

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        """Return the next scripted text response."""
        if self._raises is not None:
            raise self._raises

        class _Result:
            def __init__(self, text: str) -> None:
                self.text = text
                self.model = "stub-model"
                self.finish_reason = "STOP"
                self.blocked = False
                self.usage: dict[str, int] = {}

        return _Result(self._text.pop(0) if self._text else "")

    async def stream(self, prompt: str, **kwargs: Any) -> AsyncIterator[str]:
        """Yield the next scripted response in two slices."""
        if self._raises is not None:
            raise self._raises
        self.stream_calls.append(prompt)
        self.stream_kwargs.append(kwargs)
        text = self._text.pop(0) if self._text else ""
        midpoint = len(text) // 2
        for part in (text[:midpoint], text[midpoint:]):
            if part:
                yield part


class RecordingEmitter:
    """Collects the events a node emits."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record one event."""
        self.events.append((event_type, payload))

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        """Return the payloads of every event of ``event_type``."""
        return [payload for kind, payload in self.events if kind == event_type]


@pytest.fixture
def stub_gemini() -> StubGeminiService:
    """A permissive stub that allows everything and answers with placeholder text."""
    return StubGeminiService(
        json_responses=[
            {"verdict": "allow", "category": "none", "reason": "schoolwork"},
            {"intent": "explain", "reasoning": "concept question"},
        ],
        text_responses=["Photosynthesis is how plants make food."],
    )


@pytest.fixture
def emitter() -> RecordingEmitter:
    """An event emitter that records everything."""
    return RecordingEmitter()


@pytest.fixture
def agent_deps(stub_gemini: StubGeminiService, emitter: RecordingEmitter) -> AgentDeps:
    """Agent dependencies with no database and no network tools."""
    return AgentDeps(
        gemini=stub_gemini,
        db=None,
        web_search=None,
        wikipedia=None,
        doc_retrieval=None,
        emit=emitter,
    )


@pytest.fixture
def node_config(agent_deps: AgentDeps) -> dict[str, Any]:
    """A LangGraph-style config carrying the dependencies."""
    return {"configurable": {"deps": agent_deps, "thread_id": "test-thread"}}
