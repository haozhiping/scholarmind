"""
Hybrid search: three concurrent retrieval paths (English / Chinese / HyDE),
each a Milvus dense+sparse hybrid query, fused with RRF across paths.

Every query enforces user_id isolation + scope (paper_ids / folder_id) via the filter expr.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional

from common.clients.llm import embed_texts
from common.clients.milvus import build_scope_expr, hybrid_search as milvus_hybrid, sparse_encode
from common.config import settings
from common.logging import logger
from services.retrieval.query_optimizer import QueryBundle

_RRF_K = 60


@dataclass
class Scope:
    user_id: int
    folder_id: Optional[int] = None
    paper_ids: Optional[list[int]] = field(default=None)


async def _embed_one(text: str) -> list[float]:
    vecs = await embed_texts([text])
    return vecs[0] if vecs else []


async def _search_path(query_text: str, expr: str, top_k: int) -> list[dict]:
    if not query_text.strip():
        return []
    dense = await _embed_one(query_text)
    sparse = sparse_encode(query_text)
    if not dense:
        return []
    return await asyncio.to_thread(milvus_hybrid, dense, sparse, expr, top_k)


def _rrf_merge(result_lists: list[list[dict]], top_k: int) -> list[dict]:
    """Reciprocal Rank Fusion across multiple ranked lists, keyed by chunk id."""
    scores: dict[str, float] = {}
    best: dict[str, dict] = {}
    for results in result_lists:
        for rank, item in enumerate(results):
            cid = item.get("id")
            if cid is None:
                continue
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
            if cid not in best:
                best[cid] = item
    merged = []
    for cid, score in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)[:top_k]:
        item = dict(best[cid])
        item["score"] = score
        merged.append(item)
    return merged


async def hybrid_search(query_bundle: QueryBundle, scope: Scope, top_k: int | None = None) -> list[dict]:
    top_k = top_k or settings.RETRIEVAL_TOP_K
    expr = build_scope_expr(scope.user_id, scope.folder_id, scope.paper_ids)

    # Three paths: English (translated), Chinese (rewritten -> content_zh), HyDE (English probe)
    paths = [
        _search_path(query_bundle.translated_en or query_bundle.original, expr, top_k),
        _search_path(query_bundle.rewritten or query_bundle.original, expr, top_k),
    ]
    if query_bundle.hyde_doc:
        paths.append(_search_path(query_bundle.hyde_doc, expr, top_k))

    result_lists = await asyncio.gather(*paths, return_exceptions=True)
    clean: list[list[dict]] = []
    for r in result_lists:
        if isinstance(r, Exception):
            logger.debug(f"[search] path failed: {r}")
        elif r:
            clean.append(r)

    merged = _rrf_merge(clean, top_k)
    logger.info(f"[search] scope={expr} paths={len(clean)} merged={len(merged)} chunks")
    return merged
