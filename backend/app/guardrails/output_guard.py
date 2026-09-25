"""Output guard: checks applied to the model's answer before it is sent.

Two concerns:

1. **Unsafe content** -- a last line of defence over Gemini's own safety
   settings, catching the cases where an answer drifts somewhere inappropriate
   for a school audience.
2. **Citation verification** -- every URL the answer cites must actually appear
   in the tool results for this turn. A citation the agent invented is removed
   and the student is told, because a plausible-looking fake source is worse
   than no source at all.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence
from urllib.parse import urlparse

from app.guardrails.policies import POLICY, Category, GuardrailPolicy, GuardResult, Verdict

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")

#: Phrases that should never appear in an answer aimed at school students.
_UNSAFE_OUTPUT_PATTERNS: tuple[tuple[Category, re.Pattern[str]], ...] = (
    (Category.SELF_HARM, re.compile(r"\b(?:how to|ways to)\s+(?:kill yourself|self[- ]harm)\b", re.I)),
    (Category.WEAPONS, re.compile(r"\b(?:mix|combine)\b.{0,40}\bto (?:make|create)\b.{0,20}\bexplosive\b", re.I)),
    (Category.DRUGS, re.compile(r"\bstep[- ]by[- ]step\b.{0,30}\bsynthesis\b.{0,30}\b(?:meth|mdma|cocaine)\b", re.I)),
    (Category.SEXUAL_CONTENT, re.compile(r"\bexplicit sexual\b|\bgraphic sexual\b", re.I)),
    (Category.PERSONAL_DATA, re.compile(r"\b(?:GEMINI_API_KEY|TAVILY_API_KEY|SERPER_API_KEY)\s*[=:]\s*\S+")),
)

_UNSAFE_REPLACEMENT = (
    "I started answering that but it went somewhere I shouldn't take a school "
    "study session. Let's pick it up from the curriculum angle instead -- tell me "
    "the subject and chapter."
)

_CITATION_NOTE = (
    "\n\n> Note: I removed {count} link(s) from this answer because I couldn't "
    "verify them against my search results. Ask me to search again if you need "
    "sources."
)


@dataclass(slots=True)
class OutputGuardOutcome:
    """Result of the post-generation checks."""

    text: str
    verdict: Verdict = Verdict.ALLOW
    category: Category = Category.NONE
    reason: str = ""
    removed_citations: list[str] = field(default_factory=list)
    results: list[GuardResult] = field(default_factory=list)

    @property
    def modified(self) -> bool:
        """True when the guard changed the answer in any way."""
        return self.verdict is not Verdict.ALLOW or bool(self.removed_citations)


def extract_urls(text: str) -> list[str]:
    """Return every http(s) URL appearing in ``text``, in order, de-duplicated."""
    seen: set[str] = set()
    urls: list[str] = []
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,;:")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _normalise(url: str) -> str:
    """Normalise a URL for comparison (scheme/host lowercased, no trailing slash)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url.lower().rstrip("/")
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    return f"{host}{path}".lower()


def verify_citations(text: str, allowed_urls: Iterable[str]) -> tuple[str, list[str]]:
    """Strip citations that did not come from this turn's tool results.

    Args:
        text: The generated answer.
        allowed_urls: URLs that genuinely appeared in tool output.

    Returns:
        ``(cleaned_text, removed_urls)``.
    """
    allowed = {_normalise(url) for url in allowed_urls if url}
    removed: list[str] = []

    def _replace_markdown(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        if _normalise(url) in allowed:
            return match.group(0)
        removed.append(url)
        return label or "this source"

    cleaned = _MARKDOWN_LINK_RE.sub(_replace_markdown, text)

    def _replace_bare(match: re.Match[str]) -> str:
        url = match.group(0).rstrip(".,;:")
        if _normalise(url) in allowed:
            return match.group(0)
        removed.append(url)
        return "[unverified link removed]"

    cleaned = _URL_RE.sub(_replace_bare, cleaned)
    return cleaned, removed


class OutputGuard:
    """Runs the post-generation checks."""

    def __init__(self, policy: GuardrailPolicy = POLICY) -> None:
        """Create the guard with the given policy."""
        self._policy = policy

    def run(
        self,
        text: str,
        *,
        allowed_urls: Optional[Sequence[str]] = None,
    ) -> OutputGuardOutcome:
        """Check and, if necessary, repair a generated answer.

        Args:
            text: The answer produced by the agent.
            allowed_urls: URLs observed in this turn's tool results. When
                ``None``, citation verification is skipped (no tools ran, so
                any URL is model knowledge and is left alone only if the policy
                does not require verification).

        Returns:
            An :class:`OutputGuardOutcome` whose ``text`` is safe to send.
        """
        outcome = OutputGuardOutcome(text=text)

        for category, pattern in _UNSAFE_OUTPUT_PATTERNS:
            if pattern.search(text):
                logger.warning("Output guard blocked an answer: %s", category.value)
                outcome.verdict = Verdict.HARD_BLOCK
                outcome.category = category
                outcome.reason = f"unsafe output pattern: {category.value}"
                outcome.text = _UNSAFE_REPLACEMENT
                outcome.results.append(
                    GuardResult(
                        guard="output_safety",
                        verdict=Verdict.HARD_BLOCK,
                        category=category,
                        reason=outcome.reason,
                    )
                )
                return outcome

        outcome.results.append(GuardResult(guard="output_safety", verdict=Verdict.ALLOW))

        if self._policy.require_citation_verification and allowed_urls is not None:
            cleaned, removed = verify_citations(text, allowed_urls)
            if removed:
                logger.warning("Output guard removed %d unverified citation(s).", len(removed))
                outcome.text = cleaned + _CITATION_NOTE.format(count=len(removed))
                outcome.removed_citations = removed
                outcome.verdict = Verdict.SOFT_BLOCK
                outcome.category = Category.UNVERIFIED_CITATION
                outcome.reason = f"removed {len(removed)} unverified citation(s)"
                outcome.results.append(
                    GuardResult(
                        guard="citation_check",
                        verdict=Verdict.SOFT_BLOCK,
                        category=Category.UNVERIFIED_CITATION,
                        reason=outcome.reason,
                        metadata={"removed": removed},
                    )
                )
            else:
                outcome.results.append(
                    GuardResult(guard="citation_check", verdict=Verdict.ALLOW)
                )

        return outcome
