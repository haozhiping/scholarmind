from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class IngestBatchResponse(BaseModel):
    batch_id: str
    status: str  # "pending", "processing", "completed", "failed"
    total_tasks: int
    completed_tasks: int
    failed_tasks: int
    created_at: datetime

class IngestTaskResponse(BaseModel):
    id: str
    paper_id: int
    file_name: Optional[str] = None
    status: str  # "pending", "parsing", "indexing", "completed", "failed"
    stage: str  # "parsing", "indexing", "completed", "failed"
    progress: float  # 0.0 to 100.0
    error: Optional[str] = None
    updated_at: datetime

class TaskRetryResponse(BaseModel):
    task_id: str
    status: str
    message: str
