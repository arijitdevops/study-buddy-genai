"""Step-by-step problem-solving node.

Before handing the problem to the model, any self-contained arithmetic in the
turn is evaluated with the safe calculator and the verified value is passed
along. The model is then told to use that value rather than to compute it,
which removes the most common class of tutoring error.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agents.prompts.templates import SOLVE_PROMPT
from app.agents.state import AgentDeps, AgentState, ToolRecord, get_deps
from app.agents.tools.calculator import CalculatorError, evaluate

logger = logging.getLogger(__name__)

#: Matches a bare arithmetic expression the calculator can verify.
_EXPRESSION_RE = re.compile(
    r"(?<![\w.])((?:\d+(?:\.\d+)?|\bpi\b|\be\b|sqrt|sin|cos|tan|log|abs)"
    r"[\d\s.+\-*/%^()a-z,]*?\d\s*[)\d])(?![\w.])",
    re.I,
)

_MAX_EXPRESSIONS = 3


def _candidate_expressions(text: str) -> list[str]:
    """Extract up to ``_MAX_EXPRESSIONS`` calculator-checkable expressions."""
    found: list[str] = []
    for match in _EXPRESSION_RE.finditer(text):
        candidate = match.group(1).strip().replace("^", "**").replace("×", "*")
        if len(candidate) < 3 or candidate.replace(".", "").isdigit():
            continue
        if not any(op in candidate for op in "+-*/%(") :
            continue
        if candidate not in found:
            found.append(candidate)
        if len(found) >= _MAX_EXPRESSIONS:
            break
    return found


async def solve_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Build the step-by-step solving prompt, with verified arithmetic.

    Args:
        state: Current agent state.
        config: LangGraph config carrying :class:`AgentDeps`.

    Returns:
        A partial state update with ``compose_prompt`` and any tool records.
    """
    deps: AgentDeps = get_deps(config)
    question = state.get("effective_message") or state.get("student_message") or ""
    grade = int(state.get("grade", 8))

    verified: list[str] = []
    records: list[ToolRecord] = list(state.get("tool_results") or [])

    for expression in _candidate_expressions(question):
        try:
            result = evaluate(expression)
        except CalculatorError as exc:
            logger.debug("Calculator skipped %r: %s", expression, exc)
            continue
        verified.append(f"{result.expression} = {result.formatted}")
        records.append(
            ToolRecord(
                tool="calculator",
                query=result.expression,
                ok=True,
                detail=result.formatted,
                urls=[],
            )
        )
        await deps.emit_event(
            "tool_call",
            {"tool": "calculator", "status": "done", "detail": f"{result.expression} = {result.formatted}"},
        )

    if verified:
        tool_notes = (
            "\n\nArithmetic already verified by an exact calculator -- use these "
            "values and do not recompute them:\n"
            + "\n".join(f"- {line}" for line in verified)
        )
        logger.info("Solve node verified %d expression(s).", len(verified))
    else:
        tool_notes = ""

    return {
        "compose_prompt": SOLVE_PROMPT.format(
            question=question, grade=grade, tool_notes=tool_notes
        ),
        "tool_results": records,
    }
