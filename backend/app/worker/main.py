"""RQ worker — listens on 'ingest' queue, runs parsing → indexing pipeline.

Start (container): python -m app.worker.main
The worker auto-discovers job functions registered here via @job decorator.
"""
import os
import sys
import traceback
from datetime import datetime

from redis import Redis
from rq import Queue, Worker, Connection

from common.config import settings
from common.clients.minio import get_minio_client
from common.logging import logger


# ============================================================================
# Job handler — entry point for each queued paper
# ============================================================================

async def _run_parse_pipeline(user_id: int, paper_id: int, pdf_key: str, task_id: str) -> None:
    """Core pipeline: parse PDF → index chunks into Milvus.
    
    Imported lazily so worker startup doesn't require all service deps.
    """
    from common.db.mysql_client import mysql

    # --- Stage 0: Download PDF from MinIO ---
    logger.info(f"[task:{task_id}] Downloading PDF from MinIO: {pdf_key}")
    client = get_minio_client()
    try:
        response = client.get_object(settings.MINIO_BUCKET_PDF, pdf_key)
        pdf_bytes = response.read()
        response.close()
        response.release_conn()
    except Exception as e:
        logger.error(f"[task:{task_id}] MinIO download failed: {e}")
        await mysql.execute(
            "UPDATE ingest_tasks SET stage='failed', error_msg=%s, "
            "finished_at=%s WHERE task_id=%s",
            f"MinIO download failed: {e}", datetime.now(), task_id,
        )
        await mysql.execute(
            "UPDATE papers SET status='failed' WHERE id=%s", paper_id,
        )
        return

    # --- Stage 1: Parsing ---
    logger.info(f"[task:{task_id}] Starting parse for paper {paper_id}")
    await mysql.execute(
        "UPDATE ingest_tasks SET status='processing', stage='parsing', progress=10, "
        "started_at=%s WHERE task_id=%s",
        datetime.now(), task_id,
    )

    try:
        from services.parsing.parser import parse_paper
        parse_result = await parse_paper(
            user_id=user_id,
            paper_id=paper_id,
            pdf_key=pdf_key,
            pdf_bytes=pdf_bytes,
            db=mysql,
        )
    except Exception:
        await mysql.execute(
            "UPDATE ingest_tasks SET status='failed', stage='failed', error_msg=%s, "
            "finished_at=%s WHERE task_id=%s",
            traceback.format_exc(), datetime.now(), task_id,
        )
        await mysql.execute(
            "UPDATE papers SET status='failed' WHERE id=%s", paper_id,
        )
        return

    # --- Stage 2: Indexing ---
    await mysql.execute(
        "UPDATE ingest_tasks SET stage='indexing', progress=50 WHERE task_id=%s",
        task_id,
    )

    try:
        from services.indexing.indexer import index_paper
        await index_paper(user_id, paper_id, db=mysql)
    except Exception:
        await mysql.execute(
            "UPDATE ingest_tasks SET status='failed', stage='failed', error_msg=%s, "
            "finished_at=%s WHERE task_id=%s",
            traceback.format_exc(), datetime.now(), task_id,
        )
        await mysql.execute(
            "UPDATE papers SET status='failed' WHERE id=%s", paper_id,
        )
        return

    # --- Done ---
    await mysql.execute(
        "UPDATE ingest_tasks SET status='completed', stage='completed', progress=100, "
        "finished_at=%s WHERE task_id=%s",
        datetime.now(), task_id,
    )
    await mysql.execute(
        "UPDATE papers SET status='done' WHERE id=%s", paper_id,
    )
    logger.info(f"[task:{task_id}] Pipeline completed for paper {paper_id}")


def handle_ingest_job(user_id: int, paper_id: int, pdf_key: str, task_id: str) -> None:
    """RQ job entry point — synchronous wrapper for async pipeline.
    
    Registered as the job function for the 'ingest' queue.
    This function is called by RQ worker; it runs the async pipeline with asyncio.
    """
    import asyncio
    try:
        asyncio.run(_run_parse_pipeline(user_id, paper_id, pdf_key, task_id))
    except Exception as e:
        logger.error(f"[task:{task_id}] Pipeline failed with unhandled exception: {e}")
        raise


# ============================================================================
# Worker bootstrap
# ============================================================================

def start_worker():
    """Start RQ worker listening on 'ingest' queue."""
    logger.info("Starting ScholarMind RQ Worker...")

    redis_conn = Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        db=settings.REDIS_DB,
    )

    queue_name = "ingest"
    with Connection(redis_conn):
        worker = Worker([Queue(queue_name)])
        logger.info(f"Worker listening on queue: {queue_name}")
        worker.work()


if __name__ == "__main__":
    start_worker()
