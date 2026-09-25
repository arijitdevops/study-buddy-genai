"""Document question-answering node.

Retrieves the most relevant chunks of the session's uploaded files and folds
them into the prompt as an explicitly untrusted, delimited block.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from langchain_core.runnables import RunnableConfig

from app.agents.prompts.templates import DOC_QA_PROMPT, EXPLAIN_PROMPT
from app.agents.state import (
    AgentDeps,
    AgentState,
    GuardrailRecord,
    ToolRecord,
    add_notice,
    get_deps,
    record_guardrail,
)
from app.agents.tools.doc_retrieval import DocRetrievalError

logger = logging.getLogger(__name__)

_INJECTION_NOTICE = (
    "One of your uploaded files contains text that tries to give me instructions. "
    "I've treated it as ordinary content and ignored those instructions."
)
_NO_FILES_NOTICE = (
    "I couldn't find anything in your uploaded files that matches this question, "
    "so I've answered from general knowledge."
)


async def doc_qa_node(
    state: AgentState, config: RunnableConfig
) -> dict[str, Any]:
    """Retrieve document context and build the document-Q&A prompt.

    Args:
        state: Current agent state.
        config: LangGraph config carrying :class:`AgentDeps`.

    Returns:
        A partial state update.
    """
    deps: AgentDeps = get_deps(config)
    question = state.get("effective_message") or state.get("student_message") or ""
    grade = int(state.get("grade", 8))
    session_id = state.get("session_id", "")
    records: list[ToolRecord] = list(state.get("tool_results") or [])
    update: dict[str, Any] = {}

    if deps.doc_retrieval is None or deps.db is None:
        logger.warning("Doc-QA node has no retrieval dependency; falling back to explain.")
        return {
            "compose_prompt": EXPLAIN_PROMPT.format(question=question, grade=grade),
            "notices": add_notice(state, _NO_FILES_NOTICE),
        }

    await deps.emit_event("tool_call", {"tool": "doc_retrieval", "status": "running",
                                        "detail": "Looking through your files..."})
    try:
        result = await deps.doc_retrieval.retrieve(deps.db, session_id, question)
    except DocRetrievalError as exc:
        logger.warning("Document retrieval failed: %s", exc)
        records.append(ToolRecord(tool="doc_retrieval", query=question, ok=False,
                                  detail=str(exc), urls=[]))
        await deps.emit_event("tool_call", {"tool": "doc_retrieval", "status": "error",
                                            "detail": str(exc)})
        return {
            "compose_prompt": EXPLAIN_PROMPT.format(question=question, grade=grade),
            "tool_results": records,
            "notices": add_notice(state, _NO_FILES_NOTICE),
        }

    records.append(
        ToolRecord(
            tool="doc_retrieval",
            query=question,
            ok=True,
            detail=f"{len(result.chunks)} passage(s)",
            urls=[],
        )
    )
    await deps.emit_event(
        "tool_call",
        {"tool": "doc_retrieval", "status": "done",
         "detail": f"Read {len(result.chunks)} passage(s) from your files"},
    )

    if result.injection.flagged:
        logger.warning("Injection patterns in uploaded document: %s", result.injection.summary())
        update["guardrail_events"] = record_guardrail(
            state,
            GuardrailRecord(
                guard="prompt_injection",
                verdict="soft_block",
                category="prompt_injection",
                detail=result.injection.summary(),
            ),
        )
        update["notices"] = add_notice(state, _INJECTION_NOTICE)
        await deps.emit_event(
            "guardrail",
            {"guard": "prompt_injection", "verdict": "soft_block", "message": _INJECTION_NOTICE},
        )

    if not result.found:
        update.setdefault("notices", add_notice(state, _NO_FILES_NOTICE))
        update["compose_prompt"] = EXPLAIN_PROMPT.format(question=question, grade=grade)
        update["tool_results"] = records
        return update

    context = result.as_prompt_block()
    update.update(
        {
            "compose_prompt": DOC_QA_PROMPT.format(
                question=question, grade=grade, context=context
            ),
            "retrieved_context": context,
            "tool_results": records,
        }
    )
    return update
