"""
Observability router — real data from MySQL (query_logs / access_logs) + Milvus chunk count.
"""
import json
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.schemas.observability import AccessLogResponse, QueryLogResponse, StatsOverviewResponse
from common.auth import get_current_user_id
from common.db.mysql import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(tags=["observability"])


@router.get("/logs/queries", response_model=List[QueryLogResponse], summary="查询日志")
async def list_query_logs(limit: int = 20, offset: int = 0,
                          user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("""
            SELECT id, user_id, question, rewritten_query, retrieved_chunk_ids,
                   latency_ms, prompt_tokens, completion_tokens, feedback, created_at
            FROM query_logs WHERE user_id = :u ORDER BY id DESC LIMIT :lim OFFSET :off
        """),
        {"u": user_id, "lim": limit, "off": offset},
    )
    out = []
    for r in res.mappings().all():
        chunk_ids = r.get("retrieved_chunk_ids")
        if isinstance(chunk_ids, str):
            try:
                chunk_ids = json.loads(chunk_ids)
            except json.JSONDecodeError:
                chunk_ids = []
        out.append(QueryLogResponse(
            id=r["id"], user_id=r["user_id"], question=r["question"],
            answer_snippet="", latency_ms=r.get("latency_ms") or 0,
            tokens_used=(r.get("completion_tokens") or 0) + (r.get("prompt_tokens") or 0),
            rewritten_query=r.get("rewritten_query"),
            retrieved_chunk_ids=chunk_ids or [],
            prompt_tokens=r.get("prompt_tokens"), completion_tokens=r.get("completion_tokens"),
            feedback=r.get("feedback"), created_at=r["created_at"],
        ))
    return out


@router.get("/logs/access", response_model=List[AccessLogResponse], summary="访问日志")
async def list_access_logs(limit: int = 20, offset: int = 0,
                           user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        text("""
            SELECT id, user_id, method, path, status_code, ip, latency_ms, created_at
            FROM access_logs ORDER BY id DESC LIMIT :lim OFFSET :off
        """),
        {"lim": limit, "off": offset},
    )
    return [
        AccessLogResponse(
            id=r["id"], user_id=r.get("user_id"), path=r["path"], method=r["method"],
            status_code=r["status_code"], ip_address=r.get("ip") or "-", created_at=r["created_at"],
        )
        for r in res.mappings().all()
    ]


@router.get("/stats/overview", response_model=StatsOverviewResponse, summary="系统概览统计")
async def get_stats_overview(user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    paper_count = (await db.execute(
        text("SELECT COUNT(*) FROM papers WHERE user_id = :u"), {"u": user_id})).scalar() or 0
    chunk_count = (await db.execute(
        text("SELECT COALESCE(SUM(chunk_count), 0) FROM papers WHERE user_id = :u"), {"u": user_id})).scalar() or 0
    total_queries = (await db.execute(
        text("SELECT COUNT(*) FROM query_logs WHERE user_id = :u"), {"u": user_id})).scalar() or 0
    avg_latency = (await db.execute(
        text("SELECT COALESCE(AVG(latency_ms), 0) FROM query_logs WHERE user_id = :u"), {"u": user_id})).scalar() or 0

    return StatsOverviewResponse(
        paper_count=int(paper_count),
        chunk_count=int(chunk_count),
        total_queries=int(total_queries),
        average_latency_ms=round(float(avg_latency), 1),
    )
