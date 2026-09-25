"""Personally identifiable information detection and redaction.

Student messages are redacted **before** they are logged or written to MySQL.
The model still sees the original text for the current turn -- redacting the
prompt would make "check my address format" style homework impossible -- but
nothing identifying is persisted.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)[\s.-]?|\d{2,4}[\s.-])?\d{3}[\s.-]?\d{4}(?!\d)"
)
_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,4}"
    r"(?:Street|St|Road|Rd|Avenue|Ave|Lane|Ln|Drive|Dr|Boulevard|Blvd|Nagar|Marg|Colony)\b\.?",
    re.IGNORECASE,
)
_CREDIT_CARD_RE = re.compile(r"(?<!\d)(?:\d[ -]?){13,16}(?!\d)")
_AADHAAR_RE = re.compile(r"(?<!\d)\d{4}\s\d{4}\s\d{4}(?!\d)")

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", _EMAIL_RE),
    ("aadhaar", _AADHAAR_RE),
    ("credit_card", _CREDIT_CARD_RE),
    ("phone", _PHONE_RE),
    ("address", _ADDRESS_RE),
)


@dataclass(slots=True)
class PIIReport:
    """Result of scanning a piece of text for PII."""

    redacted_text: str
    kinds: list[str] = field(default_factory=list)
    count: int = 0

    @property
    def found(self) -> bool:
        """True when at least one PII match was detected."""
        return self.count > 0

    def summary(self) -> str:
        """Human-readable one-liner for the audit trail."""
        if not self.found:
            return "no PII detected"
        return f"redacted {self.count} item(s): {', '.join(sorted(set(self.kinds)))}"


def scan_and_redact(text: str) -> PIIReport:
    """Redact PII from ``text``.

    Args:
        text: Raw user or model text.

    Returns:
        A :class:`PIIReport` with the redacted text and what was found.

    Example:
        >>> scan_and_redact("mail me at a@b.com").redacted_text
        'mail me at [REDACTED_EMAIL]'
    """
    if not text:
        return PIIReport(redacted_text=text)

    kinds: list[str] = []
    result = text
    for kind, pattern in _PATTERNS:
        placeholder = f"[REDACTED_{kind.upper()}]"

        def _replace(match: re.Match[str], _kind: str = kind, _ph: str = placeholder) -> str:
            kinds.append(_kind)
            return _ph

        result = pattern.sub(_replace, result)

    report = PIIReport(redacted_text=result, kinds=kinds, count=len(kinds))
    if report.found:
        logger.info("PII guard: %s", report.summary())
    return report


def contains_pii(text: str) -> bool:
    """Return True when ``text`` matches any PII pattern."""
    return any(pattern.search(text) for _, pattern in _PATTERNS)
