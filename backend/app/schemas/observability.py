from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class QueryLogResponse(BaseModel):
    id: int
    user_id: int
    question: str
    answer_snippet: Optional[str] = None   # query_logs 不存储完整答案
    rewritten_query: Optional[str] = None
    latency_ms: Optional[int] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    tokens_used: Optional[int] = None       # prompt_tokens + completion_tokens
    retrieved_chunk_ids: Optional[List] = None  # JSON 数组，兼容 int/str
    feedback: Optional[int] = None           # 1=赞, -1=踩, NULL=无
    created_at: datetime

class AccessLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    path: str
    method: str
    status_code: int
    ip_address: Optional[str] = None    # DB 列名是 ip
    latency_ms: Optional[int] = None   # 来自 access_logs 表
    created_at: datetime

class StatsOverviewResponse(BaseModel):
    paper_count: int
    chunk_count: int
    total_queries: int
    average_latency_ms: float
