"""
Ingest router — real progress from MySQL ingest_batches / ingest_tasks + RQ retry.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.ingest import IngestBatchResponse, IngestTaskResponse, TaskRetryResponse
from common.auth import get_current_user_id
from common.clients.redis import get_ingest_queue
from common.db.mysql import get_db
from common.logging import logger

router = APIRouter(prefix="/ingest", tags=["ingest"])

_BATCH_STATUS = {"running": "processing", "done": "completed"}


@router.get("/batches/{batch_id}", response_model=IngestBatchResponse, summary="批次解析进度")
async def get_batch_progress(batch_id: str, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("SELECT id, total, done, failed, status, created_at FROM ingest_batches WHERE id = :b AND user_id = :u"),
        {"b": int(batch_id) if batch_id.isdigit() else -1, "u": user_id},
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="批次不存在")
    status = _BATCH_STATUS.get(row["status"], row["status"])
    if row["failed"] and row["failed"] == row["total"]:
        status = "failed"
    return IngestBatchResponse(
        batch_id=str(row["id"]), status=status, total_tasks=row["total"],
        completed_tasks=row["done"], failed_tasks=row["failed"], created_at=row["created_at"],
    )


@router.get("/tasks", response_model=List[IngestTaskResponse], summary="解析任务列表")
async def list_tasks(batch_id: Optional[str] = None, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    sql = """
        SELECT id, paper_id, stage, progress, error_msg,
               COALESCE(finished_at, started_at, created_at) AS updated_at
        FROM ingest_tasks WHERE user_id = :u
    """
    params: dict = {"u": user_id}
    if batch_id and batch_id.isdigit():
        sql += " AND batch_id = :b"
        params["b"] = int(batch_id)
    sql += " ORDER BY id DESC LIMIT 100"
    res = await db.execute(text(sql), params)
    return [
        IngestTaskResponse(
            id=str(r["id"]), paper_id=r["paper_id"] or 0, status=r["stage"], stage=r["stage"],
            progress=float(r["progress"]), error=r.get("error_msg"), updated_at=r["updated_at"],
        )
        for r in res.mappings().all()
    ]


@router.post("/tasks/{id}/retry", response_model=TaskRetryResponse, summary="重试失败任务")
async def retry_task(id: str, user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("""
            SELECT t.id, t.paper_id, p.pdf_key
            FROM ingest_tasks t JOIN papers p ON p.id = t.paper_id
            WHERE t.id = :id AND t.user_id = :u
        """),
        {"id": int(id) if id.isdigit() else -1, "u": user_id},
    )
    row = res.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    await db.execute(
        text("UPDATE ingest_tasks SET stage='queued', progress=0, error_msg=NULL, retry_count=retry_count+1 WHERE id=:id"),
        {"id": row["id"]},
    )
    await db.commit()

    try:
        get_ingest_queue().enqueue(
            "app.worker.tasks.handle_ingest_job",
            task_id=row["id"], paper_id=row["paper_id"], user_id=user_id, pdf_key=row["pdf_key"],
            job_timeout=1800,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ingest] retry enqueue failed: {e}")
        raise HTTPException(status_code=500, detail=f"重新入队失败: {e}")

    return TaskRetryResponse(task_id=str(row["id"]), status="queued", message="任务已重新入队")
