from typing import Any, Dict, List

from fastapi import APIRouter, Depends

from app.schemas.observability import (
    QueryLogResponse,
    AccessLogResponse,
    StatsOverviewResponse,
)
from common.auth.deps import get_current_user
from common.db.mysql_client import mysql

router = APIRouter(tags=["observability"])


# ---------------------------------------------------------------------------
# helpers — DB row → Pydantic
# ---------------------------------------------------------------------------

def _row_to_query_log(row: Dict[str, Any]) -> QueryLogResponse:
    """Map a MySQL query_logs row → QueryLogResponse."""
    prompt = row.get("prompt_tokens") or 0
    completion = row.get("completion_tokens") or 0

    # retrieved_chunk_ids: stored as JSON array in MySQL; the driver may
    # already deserialize it, but guard against string blobs.
    chunk_ids = row.get("retrieved_chunk_ids")
    if isinstance(chunk_ids, str):
        import json as _json
        try:
            chunk_ids = _json.loads(chunk_ids)
        except Exception:
            chunk_ids = None

    return QueryLogResponse(
        id=row["id"],
        user_id=row["user_id"],
        question=row["question"] or "",
        answer_snippet=None,
        rewritten_query=row.get("rewritten_query"),
        latency_ms=row.get("latency_ms"),
        prompt_tokens=prompt,
        completion_tokens=completion,
        tokens_used=prompt + completion if (prompt or completion) else None,
        retrieved_chunk_ids=chunk_ids,
        feedback=row.get("feedback"),
        created_at=row["created_at"],
    )


def _row_to_access_log(row: Dict[str, Any]) -> AccessLogResponse:
    """Map a MySQL access_logs row → AccessLogResponse."""
    return AccessLogResponse(
        id=row["id"],
        user_id=row.get("user_id"),
        path=row["path"],
        method=row["method"],
        status_code=row["status_code"],
        ip_address=row.get("ip"),
        latency_ms=row.get("latency_ms"),
        created_at=row["created_at"],
    )


# ---------------------------------------------------------------------------
# 1. GET /logs/queries — query history from query_logs
# ---------------------------------------------------------------------------

@router.get(
    "/logs/queries",
    response_model=List[QueryLogResponse],
    summary="查询日志",
    description="分页获取当前用户的提问记录，从 query_logs 表读取。",
)
async def list_query_logs(
    limit: int = 10,
    offset: int = 0,
    current: Dict[str, Any] = Depends(get_current_user),
):
    rows = await mysql.fetchall(
        """
        SELECT id, user_id, question, rewritten_query, retrieved_chunk_ids,
               latency_ms, prompt_tokens, completion_tokens, feedback, created_at
        FROM query_logs
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        current["id"], limit, offset,
    )
    return [_row_to_query_log(r) for r in rows]


# ---------------------------------------------------------------------------
# 2. GET /logs/access — API access history from access_logs
# ---------------------------------------------------------------------------

@router.get(
    "/logs/access",
    response_model=List[AccessLogResponse],
    summary="访问日志",
    description="分页获取当前用户的 API 访问记录，从 access_logs 表读取。",
)
async def list_access_logs(
    limit: int = 10,
    offset: int = 0,
    current: Dict[str, Any] = Depends(get_current_user),
):
    rows = await mysql.fetchall(
        """
        SELECT id, user_id, method, path, status_code, ip, latency_ms, created_at
        FROM access_logs
        WHERE user_id = %s
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        current["id"], limit, offset,
    )
    return [_row_to_access_log(r) for r in rows]


# ---------------------------------------------------------------------------
# 3. GET /stats/overview — system stats
# ---------------------------------------------------------------------------

@router.get(
    "/stats/overview",
    response_model=StatsOverviewResponse,
    summary="系统概览统计",
    description="返回系统核心指标：已入库论文总数、向量 chunk 总数、历史查询总次数、平均问答延迟（ms）。",
)
async def get_stats_overview(
    current: Dict[str, Any] = Depends(get_current_user),
):
    # Paper count for current user
    paper_row = await mysql.fetchone(
        "SELECT COUNT(*) as cnt FROM papers WHERE user_id=%s", current["id"]
    )
    paper_count = paper_row["cnt"] if paper_row else 0

    # Chunk count (from doc_blocks)
    chunk_row = await mysql.fetchone(
        "SELECT COUNT(*) as cnt FROM doc_blocks WHERE user_id=%s", current["id"]
    )
    chunk_count = chunk_row["cnt"] if chunk_row else 0

    # Query log stats
    query_row = await mysql.fetchone(
        "SELECT COUNT(*) as cnt, COALESCE(AVG(latency_ms), 0) as avg_latency "
        "FROM query_logs WHERE user_id=%s",
        current["id"],
    )
    total_queries = query_row["cnt"] if query_row else 0
    average_latency_ms = float(query_row["avg_latency"]) if query_row else 0.0

    return StatsOverviewResponse(
        paper_count=paper_count,
        chunk_count=chunk_count,
        total_queries=total_queries,
        average_latency_ms=average_latency_ms,
    )
