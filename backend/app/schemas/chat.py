from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

class ConversationCreate(BaseModel):
    title: Optional[str] = None
    folder_id: Optional[int] = None
    paper_ids: Optional[List[int]] = None

class ConversationResponse(BaseModel):
    id: str                       # UUID (conv_id from ChatService)
    title: str
    folder_id: Optional[int] = None
    paper_ids: Optional[List[int]] = None
    created_at: datetime
    updated_at: datetime

class CitationResponse(BaseModel):
    paper_id: int                 # maps to MySQL papers.id (BIGINT)
    paper_title: str
    page_num: int
    bbox: str
    chunk_type: str               # "text", "table", "figure", "formula"
    content: str
    image_key: Optional[str] = None

class MessageResponse(BaseModel):
    id: str                       # UUID (msg_id from ChatService)
    conversation_id: str          # UUID (conv_id)
    role: str                     # "user", "assistant"
    content: str
    citations: Optional[List[CitationResponse]] = None
    created_at: datetime

class ChatQueryRequest(BaseModel):
    question: str
    conversation_id: str          # UUID
    scope_type: str = "all"       # "all", "folder", "papers"
    folder_id: Optional[int] = None
    paper_ids: Optional[List[int]] = None

class FeedbackRequest(BaseModel):
    message_id: str               # UUID
    is_positive: bool
    reason: Optional[str] = None

class FeedbackResponse(BaseModel):
    status: str
    message: str
