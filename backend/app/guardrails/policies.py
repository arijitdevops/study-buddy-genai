"""Declarative guardrail policy configuration.

Everything the guards enforce is described here as data rather than scattered
through the code, so that the policy can be reviewed by a teacher or a school
administrator without reading Python.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    """The three possible guardrail outcomes.

    ``ALLOW``
        The turn proceeds unchanged.
    ``SOFT_BLOCK``
        The turn is redirected: the assistant still helps, but in a different
        shape (for example a Socratic explanation instead of a finished essay).
    ``HARD_BLOCK``
        The turn is refused and a safe alternative is offered.
    """

    ALLOW = "allow"
    SOFT_BLOCK = "soft_block"
    HARD_BLOCK = "hard_block"


class Category(str, Enum):
    """Why a guard fired. Persisted on :class:`app.db.models.GuardrailEvent`."""

    NONE = "none"
    SELF_HARM = "self_harm"
    SEXUAL_CONTENT = "sexual_content"
    VIOLENCE = "violence"
    HATE = "hate"
    HARASSMENT = "harassment"
    ILLEGAL_ACTS = "illegal_acts"
    WEAPONS = "weapons"
    DRUGS = "drugs"
    MEDICAL_ADVICE = "medical_advice"
    PERSONAL_DATA = "personal_data"
    ACADEMIC_INTEGRITY = "academic_integrity"
    PROMPT_INJECTION = "prompt_injection"
    OFF_TOPIC = "off_topic"
    TOO_LONG = "too_long"
    TOO_MANY_FILES = "too_many_files"
    UNSAFE_OUTPUT = "unsafe_output"
    UNVERIFIED_CITATION = "unverified_citation"


@dataclass(frozen=True, slots=True)
class BlockedTopic:
    """A category of content the assistant must not produce for students."""

    category: Category
    description: str
    keywords: tuple[str, ...]
    verdict: Verdict = Verdict.HARD_BLOCK


#: Topic categories refused outright, with the keyword pre-filter that catches
#: the obvious cases before any model call is made.
BLOCKED_TOPICS: tuple[BlockedTopic, ...] = (
    BlockedTopic(
        category=Category.SELF_HARM,
        description="Self-harm, suicide or disordered eating.",
        keywords=("kill myself", "suicide", "self harm", "self-harm", "cut myself", "end my life"),
    ),
    BlockedTopic(
        category=Category.SEXUAL_CONTENT,
        description="Sexual or adult content of any kind.",
        keywords=("porn", "nsfw", "sex scene", "erotic", "nude photo"),
    ),
    BlockedTopic(
        category=Category.WEAPONS,
        description="Building weapons, explosives or incendiary devices.",
        keywords=("make a bomb", "build a bomb", "pipe bomb", "napalm", "ghost gun", "3d print a gun"),
    ),
    BlockedTopic(
        category=Category.DRUGS,
        description="Obtaining or synthesising illegal drugs, alcohol or vapes.",
        keywords=("how to make meth", "buy weed", "get drunk without", "synthesise mdma", "vape without"),
    ),
    BlockedTopic(
        category=Category.ILLEGAL_ACTS,
        description="Hacking, fraud, shoplifting and other criminal how-tos.",
        keywords=("hack my school", "hack someone", "steal a car", "credit card generator", "ddos"),
    ),
    BlockedTopic(
        category=Category.HATE,
        description="Slurs, dehumanising language or hateful stereotypes.",
        keywords=(),  # handled by the model classifier and Gemini safety settings
    ),
    BlockedTopic(
        category=Category.HARASSMENT,
        description="Bullying a named classmate or writing abusive messages.",
        keywords=("write a mean message", "insult my classmate", "roast my teacher"),
    ),
    BlockedTopic(
        category=Category.MEDICAL_ADVICE,
        description="Diagnosis, dosage or treatment advice.",
        keywords=("how many pills", "what dose of", "diagnose me"),
        verdict=Verdict.SOFT_BLOCK,
    ),
)


#: Subjects the assistant is scoped to. A turn outside this list is redirected,
#: not refused -- the router sends it to smalltalk or a gentle "let's get back
#: to studying" response.
ALLOWED_SUBJECTS: tuple[str, ...] = (
    "mathematics",
    "physics",
    "chemistry",
    "biology",
    "science",
    "computer science",
    "english",
    "literature",
    "history",
    "geography",
    "civics",
    "economics",
    "environmental studies",
    "languages",
    "art",
    "music",
    "physical education",
    "general knowledge",
    "study skills",
)


@dataclass(frozen=True, slots=True)
class ReadingLevel:
    """Vocabulary and depth targets for one band of school grades."""

    label: str
    grades: tuple[int, ...]
    max_sentence_words: int
    vocabulary: str
    guidance: str


#: Reading-level bands used to shape the system prompt.
READING_LEVELS: tuple[ReadingLevel, ...] = (
    ReadingLevel(
        label="early primary",
        grades=(1, 2, 3),
        max_sentence_words=12,
        vocabulary="everyday words a 6-9 year old uses",
        guidance=(
            "Use very short sentences. Use one idea per sentence. Compare things to "
            "toys, food and games. Never use algebra notation. Finish with one "
            "encouraging line."
        ),
    ),
    ReadingLevel(
        label="upper primary",
        grades=(4, 5),
        max_sentence_words=16,
        vocabulary="simple words, with new terms defined the first time",
        guidance=(
            "Introduce one technical word at a time and define it immediately. "
            "Use concrete examples before any general rule."
        ),
    ),
    ReadingLevel(
        label="middle school",
        grades=(6, 7, 8),
        max_sentence_words=20,
        vocabulary="standard subject vocabulary with brief definitions",
        guidance=(
            "Show the reasoning in numbered steps. Use one worked example, then "
            "one similar practice question for the student to try."
        ),
    ),
    ReadingLevel(
        label="secondary",
        grades=(9, 10),
        max_sentence_words=24,
        vocabulary="full subject vocabulary; assume prior years are known",
        guidance=(
            "Give the formal statement and the intuition behind it. Mention common "
            "exam mistakes. Keep derivations complete but tight."
        ),
    ),
    ReadingLevel(
        label="senior secondary",
        grades=(11, 12),
        max_sentence_words=30,
        vocabulary="rigorous, exam-board level terminology",
        guidance=(
            "Be precise about assumptions and edge cases. Where a result has a "
            "proof the student is expected to reproduce, include it."
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    """The complete policy the guards enforce."""

    max_input_chars: int = 4000
    min_input_chars: int = 1
    max_files_per_message: int = 3
    max_attachment_chars: int = 200_000
    blocked_topics: tuple[BlockedTopic, ...] = BLOCKED_TOPICS
    allowed_subjects: tuple[str, ...] = ALLOWED_SUBJECTS
    reading_levels: tuple[ReadingLevel, ...] = READING_LEVELS
    redact_pii_before_logging: bool = True
    require_citation_verification: bool = True
    injection_scan_enabled: bool = True
    classifier_enabled: bool = True
    #: When the Gemini classifier is unreachable, do we allow the turn through?
    #: ``True`` keeps the app usable offline; the deterministic filters still run.
    fail_open_on_classifier_error: bool = True
    extra: dict[str, str] = field(default_factory=dict)

    def reading_level_for(self, grade: int) -> ReadingLevel:
        """Return the reading level band containing ``grade``.

        Args:
            grade: School class, 1-12. Out-of-range values are clamped.
        """
        clamped = min(12, max(1, grade))
        for level in self.reading_levels:
            if clamped in level.grades:
                return level
        return self.reading_levels[-1]

    def blocked_topic_for(self, text: str) -> Optional[BlockedTopic]:
        """Return the first blocked topic whose keyword appears in ``text``.

        This is a cheap deterministic pre-filter, not the whole story: the
        Gemini classifier in :mod:`app.guardrails.input_guard` catches the
        paraphrases that keywords miss.
        """
        lowered = text.lower()
        for topic in self.blocked_topics:
            for keyword in topic.keywords:
                if keyword in lowered:
                    return topic
        return None


#: The policy instance the application uses.
POLICY = GuardrailPolicy()


@dataclass(slots=True)
class GuardResult:
    """The outcome of running one guard.

    Attributes:
        guard: Name of the guard that produced this result.
        verdict: :class:`Verdict` for the turn.
        category: Why the guard fired.
        reason: Short explanation suitable for the audit trail.
        student_message: Text shown to the student when the turn is blocked or
            redirected. ``None`` when the turn is allowed.
        replacement_prompt: When a guard rewrites a turn (for example the
            academic-integrity guard turning "write my essay" into a Socratic
            coaching request), the rewritten instruction goes here.
        metadata: Anything else worth recording.
    """

    guard: str
    verdict: Verdict = Verdict.ALLOW
    category: Category = Category.NONE
    reason: str = ""
    student_message: Optional[str] = None
    replacement_prompt: Optional[str] = None
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        """True when the turn may continue without being refused."""
        return self.verdict is not Verdict.HARD_BLOCK

    @property
    def blocked(self) -> bool:
        """True when the turn must be refused outright."""
        return self.verdict is Verdict.HARD_BLOCK
