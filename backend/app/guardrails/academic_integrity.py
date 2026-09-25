"""Academic-integrity guard.

A request like "write my assignment for me" is not dangerous -- it is a
teaching moment. Rather than refusing flatly, this guard detects the intent and
*rewrites the turn* so the agent produces a Socratic response: the same topic,
but scaffolding instead of a finished artefact the student can submit.

Graded, timed work ("I'm in an exam right now, give me the answers") is treated
more strictly and is refused, because helping there is cheating no matter how
it is framed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.guardrails.policies import Category, GuardResult, Verdict

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IntegritySignal:
    """One detectable academic-integrity pattern."""

    name: str
    pattern: re.Pattern[str]
    weight: int
    live_assessment: bool = False


#: Patterns that suggest the student wants work done *for* them.
SIGNALS: tuple[IntegritySignal, ...] = (
    IntegritySignal("write_for_me", re.compile(
        r"\b(?:write|do|complete|finish)\s+(?:my|the)\s+"
        r"(?:assignment|homework|essay|project|report|lab report|coursework)\b", re.I), 3),
    IntegritySignal("just_the_answer", re.compile(
        r"\b(?:just|only)\s+(?:give|tell)\s+me\s+the\s+answers?\b", re.I), 3),
    IntegritySignal("answer_key", re.compile(
        r"\banswer key\b|\bsolutions? manual\b|\bmark scheme answers\b", re.I), 3),
    IntegritySignal("no_working", re.compile(
        r"\b(?:without|no need for)\s+(?:the\s+)?(?:working|steps|explanation)\b", re.I), 2),
    IntegritySignal("submit_as_mine", re.compile(
        r"\bso (?:i|I) can (?:submit|hand (?:it )?in|turn it in)\b", re.I), 3),
    IntegritySignal("plagiarism_evasion", re.compile(
        r"\b(?:bypass|beat|avoid|fool)\s+(?:turnitin|plagiarism|ai detector|ai detection)\b", re.I), 4),
    IntegritySignal("sound_human", re.compile(
        r"\bmake it (?:sound|look) like (?:a human|i) wrote\b", re.I), 4),
    IntegritySignal("live_exam", re.compile(
        r"\b(?:i am|i'm|currently)\s+(?:in|sitting|taking|writing)\s+(?:an?\s+)?"
        r"(?:exam|test|quiz|assessment)\b", re.I), 5, live_assessment=True),
    IntegritySignal("timed_test", re.compile(
        r"\b(?:timed|proctored|online)\s+(?:exam|test|assessment)\b.{0,40}\banswers?\b", re.I),
        5, live_assessment=True),
)

#: Score at or above which the guard treats the turn as a request to outsource
#: the work rather than to learn.
REWRITE_THRESHOLD = 3

_SOCRATIC_TEMPLATE = """The student asked me to produce finished work for them. \
Do NOT produce a submittable artefact (no complete essay, no filled-in answer \
sheet, no ready-to-hand-in report).

Instead, coach them through it for their own topic:

1. Restate what the task is actually asking for, in one or two sentences.
2. Break it into 3-5 concrete steps they can do themselves.
3. Work through ONE small illustrative example or one paragraph outline so they \
can see the shape of a good answer -- clearly labelled as an example, not as \
their answer.
4. Ask them two questions that move them to the next step.
5. Offer to check their attempt once they have written it.

Keep the tone warm and never lecture them about cheating. Their original \
message was:

{original}"""

_LIVE_ASSESSMENT_MESSAGE = (
    "It sounds like this is for a test or exam that's happening right now, so I'm "
    "going to sit this one out -- giving you answers during an assessment wouldn't "
    "be fair to you or your classmates.\n\n"
    "Come back to me the moment it's over and I'll walk you through every question "
    "properly, including the ones you weren't sure about."
)

_REDIRECT_NOTICE = (
    "I won't write this one for you, but I'll do something more useful: I'll show "
    "you how to build it yourself, step by step."
)


def evaluate(text: str) -> GuardResult:
    """Evaluate a student turn for academic-integrity concerns.

    Args:
        text: The student's message.

    Returns:
        A :class:`GuardResult`. ``ALLOW`` for ordinary help requests,
        ``SOFT_BLOCK`` with a ``replacement_prompt`` when the turn should be
        turned into Socratic coaching, ``HARD_BLOCK`` for a live assessment.
    """
    matched: list[str] = []
    score = 0
    live = False

    for signal in SIGNALS:
        if signal.pattern.search(text):
            matched.append(signal.name)
            score += signal.weight
            live = live or signal.live_assessment

    if not matched:
        return GuardResult(guard="academic_integrity", verdict=Verdict.ALLOW)

    if live:
        logger.info("Academic-integrity guard: live assessment detected (%s).", matched)
        return GuardResult(
            guard="academic_integrity",
            verdict=Verdict.HARD_BLOCK,
            category=Category.ACADEMIC_INTEGRITY,
            reason=f"live assessment signals: {', '.join(matched)}",
            student_message=_LIVE_ASSESSMENT_MESSAGE,
            metadata={"signals": matched, "score": score},
        )

    if score >= REWRITE_THRESHOLD:
        logger.info("Academic-integrity guard: rewriting turn (%s).", matched)
        return GuardResult(
            guard="academic_integrity",
            verdict=Verdict.SOFT_BLOCK,
            category=Category.ACADEMIC_INTEGRITY,
            reason=f"do-it-for-me signals: {', '.join(matched)}",
            student_message=_REDIRECT_NOTICE,
            replacement_prompt=_SOCRATIC_TEMPLATE.format(original=text.strip()),
            metadata={"signals": matched, "score": score},
        )

    return GuardResult(
        guard="academic_integrity",
        verdict=Verdict.ALLOW,
        reason=f"weak signals only: {', '.join(matched)}",
        metadata={"signals": matched, "score": score},
    )
