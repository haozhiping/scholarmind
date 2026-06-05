"""Hybrid searcher — dense vector search in Milvus + RRF multi-way fusion."""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from common.clients.llm import embed_texts
from common.clients.milvus import search as milvus_search, get_milvus_client
from common.config import settings
from common.logging import logger

from .query_optimizer import QueryBundle


@dataclass
class SearchScope:
    """Restrict search to a user and optionally a folder or paper list."""
    user_id: int
    folder_id: Optional[int] = None
    paper_ids: Optional[list[int]] = None


@dataclass
class ScoredChunk:
    chunk_id: str
    content_en: str
    content_zh: str
    paper_id: int
    page_num: int
    chunk_type: str
    image_key: str = ""
    score: float = 0.0


_RRF_K = 60


def _rrf_fuse(
    result_sets: list[list[dict]],
    top_k: int,
    k: int = _RRF_K,
) -> list[ScoredChunk]:
    """Reciprocal Rank Fusion across multiple ranked lists.

    For each unique chunk id, score = Σ 1/(k + rank_i).
    Returns deduplicated ScoredChunks sorted by score descending.
    """
    scores: dict[str, float] = {}
    entities: dict[str, dict] = {}

    for results in result_sets:
        for rank, hit in enumerate(results):
            chunk_id = hit.get("id", "")
            if not chunk_id:
                continue
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank + 1)
            if chunk_id not in entities:
                entities[chunk_id] = hit

    # Sort by RRF score descending
    sorted_ids = sorted(scores, key=scores.get, reverse=True)[:top_k]

    return [
        ScoredChunk(
            chunk_id=cid,
            content_en=entities[cid].get("content_en", ""),
            content_zh=entities[cid].get("content_zh", ""),
            paper_id=entities[cid].get("paper_id", 0),
            page_num=entities[cid].get("page_num", 0),
            chunk_type=entities[cid].get("chunk_type", ""),
            image_key=entities[cid].get("image_key", ""),
            score=scores[cid],
        )
        for cid in sorted_ids
    ]


def _build_filter_expr(scope: SearchScope) -> str:
    """Build Milvus filter expression from scope."""
    expr = f"user_id == {scope.user_id}"
    if scope.paper_ids and len(scope.paper_ids) == 1:
        expr += f" && paper_id == {scope.paper_ids[0]}"
    elif scope.paper_ids and len(scope.paper_ids) > 1:
        ids = ", ".join(str(pid) for pid in scope.paper_ids)
        expr += f" && paper_id in [{ids}]"
    if scope.folder_id is not None:
        expr += f" && folder_id == {scope.folder_id}"
    return expr


async def _dense_search(
    query_text: str,
    scope: SearchScope,
    top_k: int,
) -> list[dict]:
    """Run a single dense vector search lane."""
    if not query_text.strip():
        return []
    try:
        vecs = await embed_texts([query_text])
        query_vec = vecs[0]
    except Exception as e:
        logger.error(f"Embedding failed for query '{query_text[:60]}...': {e}")
        return []

    filter_expr = _build_filter_expr(scope)
    try:
        results = milvus_search(
            query_vector=query_vec,
            user_id=scope.user_id,
            limit=top_k,
        )
        return results
    except Exception as e:
        logger.error(f"Milvus dense search failed: {e}")
        return []


async def hybrid_search(
    bundle: QueryBundle,
    scope: SearchScope,
    top_k: Optional[int] = None,
) -> list[ScoredChunk]:
    """Multi-lane hybrid search with RRF fusion.
    
    Lanes (concurrent):
      1. rewritten (Chinese) → search content_zh
      2. translated_en  (English) → search content_en
      3. hyde_doc (English) → search content_en
    
    All lanes carry scope filtering. Results fused via RRF.
    """
    top_k = top_k or settings.RETRIEVAL_TOP_K

    lanes = []
    labels = []

    # Lane 1: rewritten query → content_zh (Chinese channel)
    if bundle.rewritten:
        lanes.append(_dense_search(bundle.rewritten, scope, top_k))
        labels.append("rewritten")

    # Lane 2: translated query → content_en (English channel)
    if bundle.translated_en:
        lanes.append(_dense_search(bundle.translated_en, scope, top_k))
        labels.append("translated")

    # Lane 3: HyDE → content_en
    if bundle.hyde_doc:
        lanes.append(_dense_search(bundle.hyde_doc, scope, top_k))
        labels.append("hyde")

    if not lanes:
        logger.warning("No search lanes available, returning empty")
        return []

    results = await asyncio.gather(*lanes, return_exceptions=True)

    valid_results: list[list[dict]] = []
    for label, r in zip(labels, results):
        if isinstance(r, Exception):
            logger.error(f"Search lane '{label}' failed: {r}")
        else:
            valid_results.append(r)

    if not valid_results:
        return []

    fused = _rrf_fuse(valid_results, top_k)
    logger.info(f"Hybrid search: {len(valid_results)} lanes → {len(fused)} RRF-fused chunks")
    return fused
