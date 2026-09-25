"""Web search: keyless DuckDuckGo default, optional keyed providers first."""

from __future__ import annotations

from typing import Any

import pytest

from app.agents.tools import web_search
from app.agents.tools.web_search import WebSearchError, WebSearchNotConfiguredError, WebSearchTool


DEFAULT_RESULTS: list[dict[str, str]] = [
    {
        "title": "Photosynthesis - Wikipedia",
        "href": "https://en.wikipedia.org/wiki/Photosynthesis",
        "body": "Photosynthesis is a process used by plants to convert light energy...",
    },
    {
        "title": "Photosynthesis | BBC Bitesize",
        "href": "https://www.bbc.co.uk/bitesize/photosynthesis",
        "body": "Plants make glucose from carbon dioxide and water.",
    },
]


class FakeDDGS:
    """Stands in for ``ddgs.DDGS`` so no network call is made."""

    calls: list[dict[str, Any]] = []
    results: list[dict[str, str]] = []

    def __init__(self, timeout: int = 5) -> None:
        self.timeout = timeout

    def text(self, query: str, **kwargs: Any) -> list[dict[str, str]]:
        FakeDDGS.calls.append({"query": query, **kwargs})
        return list(FakeDDGS.results)


@pytest.fixture(autouse=True)
def fake_ddgs(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeDDGS.calls = []
    FakeDDGS.results = list(DEFAULT_RESULTS)
    monkeypatch.setattr(web_search, "DDGS", FakeDDGS)


def test_duckduckgo_is_the_keyless_default() -> None:
    tool = WebSearchTool(tavily_api_key="", serper_api_key="", enabled=True)
    assert tool.available
    assert tool.provider_chain == ["duckduckgo"]


def test_keyed_providers_come_first_with_duckduckgo_as_fallback() -> None:
    tool = WebSearchTool(tavily_api_key="tvly-x", serper_api_key="serp-x", enabled=True)
    assert tool.provider_chain == ["tavily", "serper", "duckduckgo"]
    pinned = WebSearchTool(
        tavily_api_key="tvly-x", serper_api_key="serp-x", enabled=True, provider="duckduckgo"
    )
    assert pinned.provider_chain == ["duckduckgo"]


async def test_duckduckgo_search_returns_results_with_safe_search() -> None:
    tool = WebSearchTool(tavily_api_key="", serper_api_key="", enabled=True, max_results=5)
    response = await tool.search("photosynthesis")
    assert response.provider == "duckduckgo"
    assert response.urls == [
        "https://en.wikipedia.org/wiki/Photosynthesis",
        "https://www.bbc.co.uk/bitesize/photosynthesis",
    ]
    assert FakeDDGS.calls[0]["safesearch"] == "on"
    assert "<<<UNTRUSTED_WEB_BEGIN" in response.as_prompt_block()


async def test_falls_back_to_duckduckgo_when_tavily_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken_tavily(self: WebSearchTool, client: Any, query: str) -> Any:
        raise WebSearchError("Tavily rejected the API key (401).")

    monkeypatch.setattr(WebSearchTool, "_search_tavily", broken_tavily)
    tool = WebSearchTool(tavily_api_key="tvly-bad", serper_api_key="", enabled=True)
    response = await tool.search("photosynthesis")
    assert response.provider == "duckduckgo"


async def test_disabled_search_raises_not_configured() -> None:
    tool = WebSearchTool(enabled=False)
    assert not tool.available
    with pytest.raises(WebSearchNotConfiguredError):
        await tool.search("anything")


async def test_empty_results_are_an_error() -> None:
    FakeDDGS.results = []
    tool = WebSearchTool(tavily_api_key="", serper_api_key="", enabled=True)
    with pytest.raises(WebSearchError):
        await tool.search("zzzz")
