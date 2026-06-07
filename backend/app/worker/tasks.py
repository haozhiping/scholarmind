"""
RQ job functions. Synchronous entry points that drive the async ingest pipeline
via asyncio.run().

handle_ingest_job: parse (MinerU/fallback) -> index (chunk/enrich/vectorize -> Milvus),
updating ingest_tasks.stage/progress and ingest_batches counters throughout.
"""
import asyncio
from datetime import datetime

from sqlalchemy import text

from common.db.mysql import get_db_session
from common.logging import logger
from services.parsing.parser import parse_paper


async def _set_stage(db, task_id: int, stage: str, progress: int, error: str | None = None) -> None:
    fields = "stage = :s, progress = :p"
    params: dict = {"s": stage, "p": progress, "id": task_id}
    if stage == "parsing":
        fields += ", started_at = :now"
        params["now"] = datetime.utcnow()
    if stage in ("done", "failed"):
        fields += ", finished_at = :now"
        params["now"] = datetime.utcnow()
    if error is not None:
        fields += ", error_msg = :err"
        params["err"] = error[:2000]
    await db.execute(text(f"UPDATE ingest_tasks SET {fields} WHERE id = :id"), params)
    await db.commit()


def handle_ingest_job(*, task_id: int, paper_id: int, user_id: int, pdf_key: str) -> dict:
    """RQ entry point. Returns a summary dict; raises on fatal failure (RQ marks job failed)."""
    logger.info(f"[worker] handle_ingest_job task={task_id} paper={paper_id} user={user_id}")

    async def _run() -> dict:
        async with get_db_session() as db:
            try:
                # --- Parsing ---
                await _set_stage(db, task_id, "parsing", 10)
                parse_result = await parse_paper(user_id=user_id, paper_id=paper_id, pdf_key=pdf_key, db=db)
                block_count = len(parse_result.blocks)

                # --- Indexing ---
                await _set_stage(db, task_id, "indexing", 50)
                chunk_count = 0
                try:
                    from services.indexing import index_paper
                    chunk_count = await index_paper(parse_result, db)
                except Exception as e:  # noqa: BLE001
                    logger.error(f"[worker] indexing failed task={task_id}: {e}")
                    # Parsing succeeded; surface indexing issue but don't lose the paper.
                    await _set_stage(db, task_id, "failed", 50, error=f"indexing: {e}")
                    await _bump_batch(db, task_id, failed=True)
                    return {"paper_id": paper_id, "blocks": block_count, "chunks": 0, "indexed": False}

                # --- Done ---
                await _set_stage(db, task_id, "done", 100)
                await _bump_batch(db, task_id, failed=False)
                return {"paper_id": paper_id, "blocks": block_count, "chunks": chunk_count, "indexed": True}

            except Exception as e:  # noqa: BLE001
                logger.error(f"[worker] task={task_id} failed: {e}")
                try:
                    await _set_stage(db, task_id, "failed", 0, error=str(e))
                    await _bump_batch(db, task_id, failed=True)
                except Exception:
                    pass
                raise

    summary = asyncio.run(_run())
    logger.info(f"[worker] task={task_id} summary={summary}")
    return summary


async def _bump_batch(db, task_id: int, failed: bool) -> None:
    """Increment the parent batch's done/failed counters and close it when complete."""
    res = await db.execute(text("SELECT batch_id FROM ingest_tasks WHERE id = :id"), {"id": task_id})
    batch_id = res.scalar()
    if not batch_id:
        return
    col = "failed" if failed else "done"
    await db.execute(text(f"UPDATE ingest_batches SET {col} = {col} + 1 WHERE id = :b"), {"b": batch_id})
    await db.execute(
        text("UPDATE ingest_batches SET status = 'done' WHERE id = :b AND (done + failed) >= total"),
        {"b": batch_id},
    )
    await db.commit()
