"""Wikipedia lookup tool.

Uses the public MediaWiki REST endpoints -- no API key needed, which makes it
the sensible default for "what is X" questions where a full web search would be
overkill. Article text is untrusted and is wrapped accordingly.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import httpx

from app.guardrails.prompt_injection import InjectionScan, scan_for_injection, wrap_untrusted

logger = logging.getLogger(__name__)

SEARCH_ENDPOINT = "https://en.wikipedia.org/w/api.php"
SUMMARY_ENDPOINT = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
USER_AGENT = "StudyBuddyGenAI/0.1 (educational sample project)"


class WikipediaError(RuntimeError):
    """The lookup failed."""


@dataclass(slots=True)
class WikipediaArticle:
    """A single Wikipedia summary."""

    title: str
    extract: str
    url: str
    thumbnail: Optional[str] = None


@dataclass(slots=True)
class WikipediaResponse:
    """The result of a Wikipedia lookup."""

    query: str
    articles: list[WikipediaArticle] = field(default_factory=list)
    injection: InjectionScan = field(default_factory=InjectionScan)

    @property
    def urls(self) -> list[str]:
        """Article URLs, for the output guard's citation check."""
        return [article.url for article in self.articles if article.url]

    def as_prompt_block(self) -> str:
        """Render the articles as an untrusted, delimited prompt block."""
        if not self.articles:
            return wrap_untrusted("No Wikipedia article found.", kind="web", ident="wiki")
        body = "\n\n".join(
            f"{article.title}\nURL: {article.url}\n{article.extract}"
            for article in self.articles
        )
        return wrap_untrusted(body, kind="web", ident="wiki")


class WikipediaTool:
    """Async Wikipedia search + summary fetch."""

    def __init__(self, *, timeout_seconds: float = 12.0, max_articles: int = 2) -> None:
        """Create the tool.

        Args:
            timeout_seconds: Per-request HTTP timeout.
            max_articles: How many summaries to fetch per lookup.
        """
        self._timeout = timeout_seconds
        self._max_articles = max_articles

    async def lookup(self, query: str) -> WikipediaResponse:
        """Search Wikipedia and fetch the top article summaries.

        Args:
            query: A topic such as ``"photosynthesis"``.

        Returns:
            A :class:`WikipediaResponse` (possibly with zero articles).

        Raises:
            WikipediaError: The Wikipedia API was unreachable.
        """
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout, headers=headers) as client:
                titles = await self._search_titles(client, query)
                articles: list[WikipediaArticle] = []
                for title in titles[: self._max_articles]:
                    article = await self._fetch_summary(client, title)
                    if article is not None:
                        articles.append(article)
        except httpx.HTTPError as exc:
            logger.warning("Wikipedia lookup failed: %s", exc)
            raise WikipediaError(f"Wikipedia is unreachable right now ({exc.__class__.__name__}).") from exc

        response = WikipediaResponse(query=query, articles=articles)
        response.injection = scan_for_injection(
            "\n".join(article.extract for article in articles)
        )
        logger.info("Wikipedia returned %d article(s) for %r.", len(articles), query)
        return response

    async def _search_titles(self, client: httpx.AsyncClient, query: str) -> list[str]:
        """Return candidate article titles for ``query``."""
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": self._max_articles,
            "format": "json",
        }
        response = await client.get(SEARCH_ENDPOINT, params=params)
        response.raise_for_status()
        payload = response.json()
        hits = payload.get("query", {}).get("search", [])
        return [str(hit["title"]) for hit in hits if hit.get("title")]

    async def _fetch_summary(
        self, client: httpx.AsyncClient, title: str
    ) -> Optional[WikipediaArticle]:
        """Fetch one article summary, returning ``None`` when unavailable."""
        url = SUMMARY_ENDPOINT.format(title=quote(title.replace(" ", "_"), safe=""))
        response = await client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json()
        extract = str(payload.get("extract", "")).strip()
        if not extract:
            return None
        page_url = (
            payload.get("content_urls", {}).get("desktop", {}).get("page")
            or f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'), safe='')}"
        )
        thumbnail = (payload.get("thumbnail") or {}).get("source")
        return WikipediaArticle(
            title=str(payload.get("title", title)),
            extract=extract,
            url=str(page_url),
            thumbnail=thumbnail,
        )


_tool: Optional[WikipediaTool] = None


def get_wikipedia_tool() -> WikipediaTool:
    """Return the process-wide :class:`WikipediaTool`."""
    global _tool
    if _tool is None:
        _tool = WikipediaTool()
    return _tool
