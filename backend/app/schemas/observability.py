from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class QueryLogResponse(BaseModel):
    id: int
    user_id: int
    question: str
    answer_snippet: str = ""
    latency_ms: int = 0
    tokens_used: int = 0
    rewritten_query: Optional[str] = None
    retrieved_chunk_ids: Optional[List] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    feedback: Optional[int] = None
    created_at: datetime

class AccessLogResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    path: str
    method: str
    status_code: int
    ip_address: str
    created_at: datetime

class StatsOverviewResponse(BaseModel):
    paper_count: int
    chunk_count: int
    total_queries: int
    average_latency_ms: float
