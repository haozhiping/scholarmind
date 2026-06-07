"""
Vectorize chunks (dense + sparse) and write to Milvus.

  - dense_vec: embed_texts(content_en or content_zh) — multilingual embedding.
  - sparse_vec: dependency-free term-frequency encoding over content_en + content_zh
    (so both languages' tokens contribute to lexical hybrid recall).
  - id = xxhash64(content_en + paper_id) — idempotent upsert.
"""
from __future__ import annotations

import asyncio

import xxhash

from common.clients.llm import embed_texts
from common.clients.milvus import insert_chunks, sparse_encode
from common.logging import logger
from services.indexing.chunker import Chunk


def _chunk_id(content_en: str, paper_id: int) -> str:
    return xxhash.xxh64(f"{content_en}::{paper_id}".encode("utf-8")).hexdigest()


async def vectorize_and_store(
    chunks: list[Chunk],
    user_id: int,
    paper_id: int,
    folder_id: int | None,
) -> int:
    """Embed + write chunks to Milvus. Returns number of rows inserted."""
    if not chunks:
        return 0

    # Dense embeddings (prefer English canonical text; fall back to zh).
    dense_inputs = [(c.content_en or c.content_zh or " ") for c in chunks]
    dense_vecs = await embed_texts(dense_inputs)

    rows: list[dict] = []
    for chunk, dense in zip(chunks, dense_vecs):
        sparse = sparse_encode(f"{chunk.content_en} {chunk.content_zh}")
        rows.append({
            "id": _chunk_id(chunk.content_en, paper_id),
            "dense_vec": dense,
            "sparse_vec": sparse,
            "content_en": (chunk.content_en or "")[:65000],
            "content_zh": (chunk.content_zh or "")[:65000],
            "user_id": int(user_id),
            "paper_id": int(paper_id),
            "folder_id": int(folder_id) if folder_id is not None else -1,
            "acl": "",
            "chunk_type": chunk.block_type,
            "section": (chunk.section or "")[:255],
            "page_num": int(chunk.page_num) if chunk.page_num is not None else -1,
            "bbox": (str(chunk.bbox) if chunk.bbox is not None else "")[:127],
            "block_id": int(chunk.block_id) if chunk.block_id is not None else -1,
            "image_key": (chunk.image_key or "")[:255],
        })

    inserted = await asyncio.to_thread(insert_chunks, rows)
    logger.info(f"[vectorize] paper={paper_id} inserted {inserted}/{len(rows)} chunks to Milvus")
    return inserted
