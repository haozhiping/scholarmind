"""
Indexing service entry point: index_paper(ParseResult, db).

Reads the paper's doc_blocks from MySQL, chunks them, enriches with Chinese summaries,
vectorizes (dense + sparse) and writes to Milvus, then updates papers.chunk_count.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from common.logging import logger
from services.indexing.chunker import chunk_blocks
from services.indexing.enricher import enrich_chunks
from services.indexing.vectorizer import vectorize_and_store


async def index_paper(parse_result, db: AsyncSession) -> int:
    """Full indexing pipeline for one parsed paper. Returns chunk count written."""
    user_id = parse_result.user_id
    paper_id = parse_result.paper_id

    # folder_id for Milvus scalar scope
    res = await db.execute(text("SELECT folder_id FROM papers WHERE id = :id"), {"id": paper_id})
    folder_id = res.scalar()

    # Read parent blocks back from MySQL (gives us block_id for small-big retrieval)
    rows = await db.execute(
        text("""
            SELECT id, block_type, content, content_zh, page_num, bbox, image_key
            FROM doc_blocks
            WHERE paper_id = :p AND user_id = :u
            ORDER BY id ASC
        """),
        {"p": paper_id, "u": user_id},
    )
    block_rows = [dict(r) for r in rows.mappings().all()]
    if not block_rows:
        logger.warning(f"[index] paper={paper_id} has no doc_blocks; nothing to index")
        return 0

    chunks = chunk_blocks(block_rows)
    chunks = await enrich_chunks(chunks)
    count = await vectorize_and_store(chunks, user_id, paper_id, folder_id)

    await db.execute(
        text("UPDATE papers SET chunk_count = :c WHERE id = :id"),
        {"c": count, "id": paper_id},
    )
    await db.commit()
    logger.info(f"[index] paper={paper_id} indexed {count} chunks")
    return count
