from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.ingest import IngestBatchResponse, IngestTaskResponse, TaskRetryResponse
from common.auth.deps import get_current_user
from common.db.mysql_client import mysql

router = APIRouter(prefix="/ingest", tags=["ingest"])


def _task_row_to_response(row: Dict[str, Any]) -> IngestTaskResponse:
    return IngestTaskResponse(
        id=row["task_id"],
        paper_id=row["paper_id"],
        file_name=row.get("file_name"),
        status=row["status"],
        stage=row["stage"],
        progress=float(row.get("progress") or 0),
        error=row.get("error_msg"),
        updated_at=row["updated_at"],
    )


@router.get("/batches/{batch_id}", response_model=IngestBatchResponse,
            summary="批次解析进度",
            description="查询一次批量上传的整体进度，返回总任务数、已完成数、失败数及状态（processing/completed/failed）。前端上传后轮询此接口。")
async def get_batch_progress(
    batch_id: str,
    current=Depends(get_current_user),
):
    tasks = await mysql.fetchall(
        "SELECT status FROM ingest_tasks WHERE batch_id=%s AND user_id=%s",
        batch_id, current["id"],
    )
    if not tasks:
        raise HTTPException(status_code=404, detail="批次不存在")

    total = len(tasks)
    completed = sum(1 for t in tasks if t["status"] == "completed")
    failed = sum(1 for t in tasks if t["status"] == "failed")

    if completed == total:
        status = "completed"
    elif failed == total:
        status = "failed"
    else:
        status = "processing"

    # Use the earliest created_at from tasks as the batch created_at
    first = await mysql.fetchone(
        "SELECT MIN(created_at) as created_at FROM ingest_tasks WHERE batch_id=%s AND user_id=%s",
        batch_id, current["id"],
    )

    return IngestBatchResponse(
        batch_id=batch_id,
        status=status,
        total_tasks=total,
        completed_tasks=completed,
        failed_tasks=failed,
        created_at=first["created_at"] if first else None,
    )


@router.get("/tasks", response_model=List[IngestTaskResponse],
            summary="解析任务列表",
            description="查询单个或所有解析任务的详细状态，包含当前阶段（queued/parsing/indexing/completed/failed）、进度百分比和错误信息。可按 `batch_id` 过滤。")
async def list_tasks(
    batch_id: Optional[str] = None,
    current=Depends(get_current_user),
):
    if batch_id:
        rows = await mysql.fetchall(
            "SELECT t.task_id, t.paper_id, t.status, t.stage, t.progress, "
            "t.error_msg, t.batch_id, t.started_at, t.finished_at, "
            "t.created_at, t.updated_at, "
            "p.title AS file_name FROM ingest_tasks t "
            "LEFT JOIN papers p ON p.id = t.paper_id "
            "WHERE t.batch_id=%s AND t.user_id=%s ORDER BY t.created_at DESC",
            batch_id, current["id"],
        )
    else:
        rows = await mysql.fetchall(
            "SELECT t.task_id, t.paper_id, t.status, t.stage, t.progress, "
            "t.error_msg, t.batch_id, t.started_at, t.finished_at, "
            "t.created_at, t.updated_at, "
            "p.title AS file_name FROM ingest_tasks t "
            "LEFT JOIN papers p ON p.id = t.paper_id "
            "WHERE t.user_id=%s ORDER BY t.created_at DESC LIMIT 50",
            current["id"],
        )
    return [_task_row_to_response(r) for r in rows]


@router.post("/tasks/{id}/retry", response_model=TaskRetryResponse,
             summary="重试失败任务",
             description="将 `failed` 状态的解析任务重新入队，从头开始解析。任务 stage 重置为 parsing，progress 重置为 0。")
async def retry_task(
    id: str,
    current=Depends(get_current_user),
):
    row = await mysql.fetchone(
        "SELECT * FROM ingest_tasks WHERE task_id=%s AND user_id=%s", id, current["id"]
    )
    if not row:
        raise HTTPException(status_code=404, detail="任务不存在")

    await mysql.execute(
        "UPDATE ingest_tasks SET status='pending', stage='parsing', progress=0, error=NULL "
        "WHERE task_id=%s AND user_id=%s",
        id, current["id"],
    )
    return TaskRetryResponse(
        task_id=id,
        status="pending",
        message="任务已重新入队",
    )
