"""HybridRetriever — compose query optimizer + searcher + reranker into a single retrieve() call."""
from typing import Optional

from common.config import settings
from common.logging import logger

from .query_optimizer import optimize_query, QueryBundle
from .searcher import hybrid_search, SearchScope, ScoredChunk
from .reranker import evaluate_and_filter


class HybridRetriever:
    """High-level retriever that chains query optimization → hybrid search → rerank → quality filter.

    Usage::

        retriever = HybridRetriever()
        results = await retriever.retrieve("什么是attention机制", top_k=5)
    """

    def __init__(self):
        pass

    async def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        user_id: Optional[int] = None,
        history: Optional[list[dict]] = None,
        scope: Optional[SearchScope] = None,
    ) -> list[dict]:
        """Execute the full retrieval pipeline and return a flat list of dicts.

        Args:
            query:   User question, in any language.
            top_k:   Number of final chunks to return (after rerank).
            user_id: Scoping filter (forwarded to SearchScope).
            history: Past conversation turns for query-rewrite context.
            scope:   Explicit SearchScope; overrides user_id when both given.

        Returns:
            List of dicts with keys: content, paper_id, chunk_id, page, score, chunk_type.
            Empty list when no relevant chunks found.
        """
        # ---- Step 1: query understanding (rewrite + translate + HyDE) ----
        bundle = await optimize_query(query, history)
        logger.debug(f"Query optimized: {len(bundle.queries)} variant(s)")

        # ---- Step 2: hybrid search (dense multi-lane + RRF fusion) ----
        search_scope = scope or SearchScope(user_id=user_id or 0)
        # Fetch more candidates so the reranker has room to filter
        candidate_k = max(top_k * 3, 15)
        chunks = await hybrid_search(bundle, search_scope, top_k=candidate_k)
        logger.debug(f"Hybrid search returned {len(chunks)} candidate chunks")

        if not chunks:
            return []

        # ---- Step 3: rerank + corrective grade ----
        filtered, status = await evaluate_and_filter(query, chunks, top_n=top_k)
        logger.info(
            f"Retrieval pipeline: {len(chunks)} candidates → {len(filtered)} final "
            f"(status={status})"
        )

        # ---- Step 4: format output (dicts for ChatService consumers) ----
        return [
            {
                "content": ch.content_zh or ch.content_en or "",
                "paper_id": ch.paper_id,
                "chunk_id": ch.chunk_id,
                "page": ch.page_num,
                "score": ch.score,
                "chunk_type": ch.chunk_type,
            }
            for ch in filtered
        ]
