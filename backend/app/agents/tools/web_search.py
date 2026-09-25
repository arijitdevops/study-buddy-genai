"""Web search tool with a keyless default and optional paid providers.

Provider chain (``WEB_SEARCH_PROVIDER=auto``, the default):

1. **Tavily** when ``TAVILY_API_KEY`` is set (search API built for LLM agents),
2. **Serper** when ``SERPER_API_KEY`` is set (Google results),
3. **DuckDuckGo** via the ``ddgs`` package -- no key, always available.

Every provider sits behind one :class:`WebSearchTool` interface returning
:class:`SearchResult` objects, so the agent never knows which one answered.
Every snippet that comes back is untrusted text and is wrapped by
:mod:`app.guardrails.prompt_injection` before it reaches a prompt.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

try:  # pragma: no cover - exercised only when the package is missing
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    _DDGS_AVAILABLE = True
except ImportError:  # pragma: no cover
    DDGS = None  # type: ignore[assignment,misc]
    DDGSException = Exception  # type: ignore[assignment,misc]
    _DDGS_AVAILABLE = False

from app.config import settings
from app.guardrails.prompt_injection import InjectionScan, scan_for_injection, wrap_untrusted

logger = logging.getLogger(__name__)

TAVILY_ENDPOINT = "https://api.tavily.com/search"
SERPER_ENDPOINT = "https://google.serper.dev/search"


class WebSearchError(RuntimeError):
    """Search could not be performed."""


class WebSearchNotConfiguredError(WebSearchError):
    """Search is disabled (or no provider can run)."""


#: Providers accepted by ``WEB_SEARCH_PROVIDER``.
PROVIDERS: tuple[str, ...] = ("auto", "tavily", "serper", "duckduckgo")


@dataclass(slots=True)
class SearchResult:
    """One search hit."""

    title: str
    url: str
    snippet: str
    provider: str
    score: Optional[float] = None


@dataclass(slots=True)
class SearchResponse:
    """The full result of a search call."""

    query: str
    provider: str
    results: list[SearchResult] = field(default_factory=list)
    answer: Optional[str] = None
    injection: InjectionScan = field(default_factory=InjectionScan)

    @property
    def urls(self) -> list[str]:
        """URLs returned, used by the output guard's citation check."""
        return [result.url for result in self.results if result.url]

    def as_prompt_block(self) -> str:
        """Render the results as an untrusted, delimited prompt block."""
        if not self.results:
            return wrap_untrusted("No results were returned.", kind="web", ident=self.query[:24])
        lines: list[str] = []
        if self.answer:
            lines.append(f"Provider summary: {self.answer}")
            lines.append("")
        for index, result in enumerate(self.results, start=1):
            lines.append(f"[{index}] {result.title}")
            lines.append(f"    URL: {result.url}")
            lines.append(f"    {result.snippet}")
            lines.append("")
        return wrap_untrusted("\n".join(lines), kind="web", ident=self.query[:24])


class WebSearchTool:
    """Async web search: Tavily / Serper when keyed, DuckDuckGo otherwise."""

    def __init__(
        self,
        tavily_api_key: Optional[str] = None,
        serper_api_key: Optional[str] = None,
        *,
        enabled: Optional[bool] = None,
        provider: Optional[str] = None,
        max_results: Optional[int] = None,
        region: Optional[str] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        """Create the tool.

        Args:
            tavily_api_key: Tavily key; defaults to settings.
            serper_api_key: Serper key; defaults to settings.
            enabled: Master switch; defaults to ``ENABLE_WEB_SEARCH``.
            provider: ``auto``, ``tavily``, ``serper`` or ``duckduckgo``;
                defaults to ``WEB_SEARCH_PROVIDER``.
            max_results: Result cap per query.
            region: DuckDuckGo region code; defaults to ``WEB_SEARCH_REGION``.
            timeout_seconds: Per-request HTTP timeout.
        """
        self._tavily = (tavily_api_key if tavily_api_key is not None else settings.tavily_api_key).strip()
        self._serper = (serper_api_key if serper_api_key is not None else settings.serper_api_key).strip()
        self._enabled = settings.enable_web_search if enabled is None else enabled
        chosen = (provider or settings.web_search_provider or "auto").strip().lower()
        if chosen not in PROVIDERS:
            logger.warning("Unknown WEB_SEARCH_PROVIDER=%r; using 'auto'.", chosen)
            chosen = "auto"
        self._provider = chosen
        self._max_results = max_results or settings.web_search_max_results
        self._region = region or settings.web_search_region
        self._timeout = timeout_seconds

    @property
    def provider_chain(self) -> list[str]:
        """Providers that will be tried, in order."""
        chain: list[str] = []
        if self._provider in ("auto", "tavily") and self._tavily:
            chain.append("tavily")
        if self._provider in ("auto", "serper") and self._serper:
            chain.append("serper")
        if _DDGS_AVAILABLE:
            chain.append("duckduckgo")
        return chain

    @property
    def available(self) -> bool:
        """True when search is enabled and at least one provider can run."""
        return self._enabled and bool(self.provider_chain)

    @property
    def unavailable_reason(self) -> str:
        """A student-friendly explanation of why search cannot run."""
        if not self._enabled:
            return (
                "Web search is switched off for this deployment "
                "(ENABLE_WEB_SEARCH=false). I'll answer from what I already know."
            )
        return (
            "I can't search the web right now because the search package isn't "
            "installed on the server (pip install ddgs). "
            "I'll answer from what I already know instead."
        )

    async def search(self, query: str) -> SearchResponse:
        """Run a search, trying each provider in :attr:`provider_chain`.

        Args:
            query: The search query.

        Returns:
            A :class:`SearchResponse`.

        Raises:
            WebSearchNotConfiguredError: Search is disabled.
            WebSearchError: Every provider failed.
        """
        if not self.available:
            raise WebSearchNotConfiguredError(self.unavailable_reason)

        errors: list[str] = []
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            for provider in self.provider_chain:
                try:
                    if provider == "tavily":
                        response = await self._search_tavily(client, query)
                    elif provider == "serper":
                        response = await self._search_serper(client, query)
                    else:
                        response = await self._search_duckduckgo(query)
                    return self._finalise(response)
                except (httpx.HTTPError, WebSearchError, ValueError, DDGSException) as exc:
                    logger.warning("%s search failed: %s", provider, exc)
                    errors.append(f"{provider}: {exc}")

        raise WebSearchError("All search providers failed: " + "; ".join(errors))

    # ------------------------------------------------------------------
    async def _search_duckduckgo(self, query: str) -> SearchResponse:
        """Query DuckDuckGo (and the other ddgs backends) -- no API key needed.

        ``ddgs`` is synchronous, so the call runs in a worker thread.
        """
        if not _DDGS_AVAILABLE:  # pragma: no cover
            raise WebSearchError("The ddgs package is not installed.")

        def _run() -> list[dict[str, Any]]:
            return list(
                DDGS(timeout=int(self._timeout)).text(
                    query,
                    region=self._region,
                    safesearch="on",
                    max_results=self._max_results,
                )
                or []
            )

        items = await asyncio.to_thread(_run)
        results = [
            SearchResult(
                title=str(item.get("title", "")).strip() or "Untitled",
                url=str(item.get("href", "")).strip(),
                snippet=str(item.get("body", "")).strip()[:1200],
                provider="duckduckgo",
            )
            for item in items[: self._max_results]
            if item.get("href")
        ]
        if not results:
            raise WebSearchError("DuckDuckGo returned no results.")
        return SearchResponse(query=query, provider="duckduckgo", results=results)

    # ------------------------------------------------------------------
    async def _search_tavily(self, client: httpx.AsyncClient, query: str) -> SearchResponse:
        """Query the Tavily API."""
        payload: dict[str, Any] = {
            "api_key": self._tavily,
            "query": query,
            "max_results": self._max_results,
            "search_depth": "basic",
            "include_answer": True,
        }
        response = await client.post(TAVILY_ENDPOINT, json=payload)
        if response.status_code == 401:
            raise WebSearchError("Tavily rejected the API key (401).")
        response.raise_for_status()
        data = response.json()
        results = [
            SearchResult(
                title=str(item.get("title", "")).strip() or "Untitled",
                url=str(item.get("url", "")).strip(),
                snippet=str(item.get("content", "")).strip()[:1200],
                provider="tavily",
                score=item.get("score"),
            )
            for item in data.get("results", [])[: self._max_results]
        ]
        answer = data.get("answer")
        return SearchResponse(
            query=query,
            provider="tavily",
            results=results,
            answer=str(answer) if answer else None,
        )

    async def _search_serper(self, client: httpx.AsyncClient, query: str) -> SearchResponse:
        """Query the Serper API."""
        response = await client.post(
            SERPER_ENDPOINT,
            json={"q": query, "num": self._max_results},
            headers={"X-API-KEY": self._serper, "Content-Type": "application/json"},
        )
        if response.status_code in (401, 403):
            raise WebSearchError(f"Serper rejected the API key ({response.status_code}).")
        response.raise_for_status()
        data = response.json()
        results = [
            SearchResult(
                title=str(item.get("title", "")).strip() or "Untitled",
                url=str(item.get("link", "")).strip(),
                snippet=str(item.get("snippet", "")).strip()[:1200],
                provider="serper",
            )
            for item in data.get("organic", [])[: self._max_results]
        ]
        answer_box = data.get("answerBox") or {}
        answer = answer_box.get("answer") or answer_box.get("snippet")
        return SearchResponse(
            query=query,
            provider="serper",
            results=results,
            answer=str(answer) if answer else None,
        )

    @staticmethod
    def _finalise(response: SearchResponse) -> SearchResponse:
        """Scan the combined snippets for injection attempts."""
        combined = "\n".join(
            f"{result.title}\n{result.snippet}" for result in response.results
        )
        if response.answer:
            combined = f"{response.answer}\n{combined}"
        response.injection = scan_for_injection(combined)
        logger.info(
            "Web search via %s returned %d result(s) for %r.",
            response.provider,
            len(response.results),
            response.query,
        )
        return response


_tool: Optional[WebSearchTool] = None


def get_web_search_tool() -> WebSearchTool:
    """Return the process-wide :class:`WebSearchTool`."""
    global _tool
    if _tool is None:
        _tool = WebSearchTool()
    return _tool
