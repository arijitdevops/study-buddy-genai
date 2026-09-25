"""FastAPI dependency providers.

Everything a route needs is resolved here so the route bodies stay about HTTP
and the services stay testable by direct construction.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.state import AgentDeps
from app.agents.tools.doc_retrieval import DocRetrievalTool, get_doc_retrieval_tool
from app.agents.tools.web_search import WebSearchTool, get_web_search_tool
from app.agents.tools.wikipedia import WikipediaTool, get_wikipedia_tool
from app.config import Settings, get_settings
from app.db.models import ChatSession
from app.db.session import get_db_session
from app.services.file_service import FileService, get_file_service
from app.services.gemini import GeminiService, get_gemini_service

logger = logging.getLogger(__name__)

DbSession = Annotated[AsyncSession, Depends(get_db_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]
Gemini = Annotated[GeminiService, Depends(get_gemini_service)]
Files = Annotated[FileService, Depends(get_file_service)]
Search = Annotated[WebSearchTool, Depends(get_web_search_tool)]
Wiki = Annotated[WikipediaTool, Depends(get_wikipedia_tool)]
Retrieval = Annotated[DocRetrievalTool, Depends(get_doc_retrieval_tool)]


async def get_chat_session(
    session_id: Annotated[str, Path(max_length=32)],
    db: DbSession,
) -> ChatSession:
    """Load a chat session or raise a 404.

    Args:
        session_id: The session's id.
        db: Active database session.

    Returns:
        The :class:`ChatSession`.

    Raises:
        HTTPException: 404 when the session does not exist.
    """
    session: Optional[ChatSession] = await db.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No chat session with id {session_id!r}.",
        )
    return session


CurrentSession = Annotated[ChatSession, Depends(get_chat_session)]


def build_agent_deps(
    db: DbSession,
    gemini: Gemini,
    search: Search,
    wiki: Wiki,
    retrieval: Retrieval,
) -> AgentDeps:
    """Assemble the dependency bundle handed to the agent graph."""
    return AgentDeps(
        gemini=gemini,
        db=db,
        web_search=search,
        wikipedia=wiki,
        doc_retrieval=retrieval,
    )


AgentDependencies = Annotated[AgentDeps, Depends(build_agent_deps)]
