"""
Retrieval service entry point: retrieve(question, scope) -> ranked chunks.

Pipeline: optimize_query (rewrite/translate/HyDE) -> hybrid_search (dense+sparse, 3-path RRF)
-> rerank -> (optional) corrective grade. Returns top chunks ready for answer generation.
"""
from __future__ import annotations

from typing import Optional

from common.config import settings
from common.logging import logger
from services.retrieval.query_optimizer import optimize_query
from services.retrieval.reranker import corrective_grade, rerank_chunks
from services.retrieval.searcher import Scope, hybrid_search


async def retrieve(
    question: str,
    user_id: int,
    *,
    folder_id: Optional[int] = None,
    paper_ids: Optional[list[int]] = None,
    history: Optional[list[dict]] = None,
    top_n: Optional[int] = None,
) -> list[dict]:
    """Full retrieval pipeline. Returns a list of ranked chunk dicts."""
    scope = Scope(user_id=user_id, folder_id=folder_id, paper_ids=paper_ids)

    bundle = await optimize_query(question, history)
    chunks = await hybrid_search(bundle, scope)
    if not chunks:
        logger.info("[retrieve] no chunks found")
        return []

    ranked = await rerank_chunks(question, chunks, top_n=top_n or settings.RERANK_TOP_N)

    if settings.ENABLE_CORRECTIVE_RAG:
        grade = await corrective_grade(question, ranked)
        if grade.get("action") == "reject":
            logger.info(f"[retrieve] corrective reject: {grade.get('reason')}")
            return []

    return ranked


__all__ = ["retrieve", "Scope"]
