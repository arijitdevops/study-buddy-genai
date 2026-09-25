"""Retrieval over the chunks extracted from a session's uploaded files.

The sample keeps retrieval dependency-free on purpose: a bag-of-words scorer
with inverse-document-frequency weighting over the session's own chunks. A
session has at most a handful of documents, so the win from a vector store
would be small compared with the operational cost of running one. The interface
is deliberately the same shape a vector retriever would have, so swapping it is
a local change.

Retrieved chunks are untrusted text and are wrapped before reaching a prompt.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FileChunk, FileStatus, UploadedFile
from app.guardrails.prompt_injection import InjectionScan, scan_for_injection, wrap_untrusted

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9']+")

_STOPWORDS: frozenset[str] = frozenset(
    """a an and are as at be but by for from has have how i in is it its of on or
    that the their there these this to was were what when where which who why
    will with you your me my do does did can could should would""".split()
)


class DocRetrievalError(RuntimeError):
    """Retrieval could not be performed."""


@dataclass(slots=True)
class RetrievedChunk:
    """One scored chunk of an uploaded document."""

    file_id: str
    file_name: str
    chunk_index: int
    content: str
    page: Optional[int]
    score: float

    def citation(self) -> str:
        """A short human-readable source label."""
        if self.page is not None:
            return f"{self.file_name}, p.{self.page}"
        return f"{self.file_name}, part {self.chunk_index + 1}"


@dataclass(slots=True)
class RetrievalResult:
    """The chunks retrieved for one question."""

    query: str
    chunks: list[RetrievedChunk] = field(default_factory=list)
    injection: InjectionScan = field(default_factory=InjectionScan)

    @property
    def found(self) -> bool:
        """True when at least one chunk was retrieved."""
        return bool(self.chunks)

    def as_prompt_block(self) -> str:
        """Render the chunks as an untrusted, delimited prompt block."""
        if not self.chunks:
            return wrap_untrusted(
                "No relevant passage was found in the uploaded files.",
                kind="document",
                ident="none",
            )
        body = "\n\n".join(
            f"[Source: {chunk.citation()}]\n{chunk.content}" for chunk in self.chunks
        )
        return wrap_untrusted(body, kind="document", ident="session-files")


def tokenize(text: str) -> list[str]:
    """Lowercase, split on word characters and drop stopwords."""
    return [
        token
        for token in _TOKEN_RE.findall(text.lower())
        if token not in _STOPWORDS and len(token) > 1
    ]


def score_chunks(query: str, chunks: Sequence[tuple[str, str]]) -> list[tuple[str, float]]:
    """Score ``chunks`` against ``query`` with a TF-IDF cosine-ish measure.

    Args:
        query: The student's question.
        chunks: ``(chunk_id, text)`` pairs.

    Returns:
        ``(chunk_id, score)`` pairs, unsorted. Scores are >= 0.
    """
    query_tokens = tokenize(query)
    if not query_tokens or not chunks:
        return [(chunk_id, 0.0) for chunk_id, _ in chunks]

    tokenised = {chunk_id: Counter(tokenize(text)) for chunk_id, text in chunks}
    total_docs = len(tokenised)
    doc_frequency: Counter[str] = Counter()
    for counts in tokenised.values():
        doc_frequency.update(counts.keys())

    query_counts = Counter(query_tokens)
    scores: list[tuple[str, float]] = []
    for chunk_id, counts in tokenised.items():
        length = sum(counts.values()) or 1
        score = 0.0
        for token, q_count in query_counts.items():
            tf = counts.get(token, 0)
            if not tf:
                continue
            idf = math.log((total_docs + 1) / (doc_frequency[token] + 1)) + 1.0
            score += (tf / length) * idf * q_count
        scores.append((chunk_id, score))
    return scores


class DocRetrievalTool:
    """Retrieves relevant passages from a session's uploaded documents."""

    def __init__(self, *, top_k: int = 5, max_chars: int = 6000) -> None:
        """Create the tool.

        Args:
            top_k: Maximum number of chunks to return.
            max_chars: Total character budget across the returned chunks.
        """
        self._top_k = top_k
        self._max_chars = max_chars

    async def retrieve(
        self, db: AsyncSession, session_id: str, query: str, *, file_ids: Optional[Sequence[str]] = None
    ) -> RetrievalResult:
        """Retrieve the most relevant chunks for ``query``.

        Args:
            db: Active database session.
            session_id: Chat session whose files should be searched.
            query: The student's question.
            file_ids: Restrict retrieval to these files when given.

        Returns:
            A :class:`RetrievalResult`.

        Raises:
            DocRetrievalError: The database query failed.
        """
        statement = (
            select(FileChunk, UploadedFile)
            .join(UploadedFile, FileChunk.file_id == UploadedFile.id)
            .where(
                UploadedFile.session_id == session_id,
                UploadedFile.status == FileStatus.READY,
            )
        )
        if file_ids:
            statement = statement.where(UploadedFile.id.in_(list(file_ids)))

        try:
            rows = (await db.execute(statement)).all()
        except SQLAlchemyError as exc:
            logger.exception("Chunk retrieval query failed.")
            raise DocRetrievalError("Could not read the uploaded files from the database.") from exc

        if not rows:
            return RetrievalResult(query=query)

        pairs = [(chunk.id, chunk.content) for chunk, _ in rows]
        by_id = {chunk.id: (chunk, file) for chunk, file in rows}
        scored = sorted(score_chunks(query, pairs), key=lambda item: item[1], reverse=True)

        selected: list[RetrievedChunk] = []
        budget = self._max_chars
        for chunk_id, score in scored[: self._top_k]:
            if score <= 0:
                continue
            chunk, file = by_id[chunk_id]
            content = chunk.content[:budget]
            if not content:
                break
            budget -= len(content)
            selected.append(
                RetrievedChunk(
                    file_id=file.id,
                    file_name=file.original_name,
                    chunk_index=chunk.chunk_index,
                    content=content,
                    page=chunk.page,
                    score=round(score, 4),
                )
            )

        # Fall back to the opening chunks when nothing matched lexically -- a
        # short document the student just uploaded is still the right context.
        if not selected:
            for chunk, file in rows[: self._top_k]:
                selected.append(
                    RetrievedChunk(
                        file_id=file.id,
                        file_name=file.original_name,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content[:1500],
                        page=chunk.page,
                        score=0.0,
                    )
                )

        result = RetrievalResult(query=query, chunks=selected)
        result.injection = scan_for_injection("\n".join(c.content for c in selected))
        logger.info("Retrieved %d chunk(s) for session %s.", len(selected), session_id)
        return result


_tool: Optional[DocRetrievalTool] = None


def get_doc_retrieval_tool() -> DocRetrievalTool:
    """Return the process-wide :class:`DocRetrievalTool`."""
    global _tool
    if _tool is None:
        _tool = DocRetrievalTool()
    return _tool
