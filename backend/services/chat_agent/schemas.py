from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
from uuid import UUID
from datetime import datetime

class Message(BaseModel):
    msg_id: str = Field(description="消息唯一ID")
    role: str = Field(description="角色：user/assistant/system")
    content: str = Field(description="消息内容")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: Optional[Dict[str, Any]] = None

class Conversation(BaseModel):
    conv_id: UUID = Field(description="会话唯一ID")
    user_id: Optional[str] = Field(default=None, description="用户ID")
    title: Optional[str] = Field(default=None, description="会话标题")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Optional[Dict[str, Any]] = None

class ChatRequest(BaseModel):
    conv_id: Optional[str] = Field(default=None, description="会话ID，为空则创建新会话")
    query: str = Field(description="用户查询")
    history: Optional[List[Dict[str, str]]] = Field(default=None, description="历史消息")
    stream: bool = Field(default=True, description="是否流式输出")

class IntentResult(BaseModel):
    intent_type: str = Field(description="意图类型：chitchat/knowledge/complex/followup")
    confidence: float = Field(description="置信度 0-1")
    reasoning: Optional[str] = Field(default=None, description="分类理由")

class CiteInfo(BaseModel):
    paper_id: str = Field(description="论文ID")
    page: Optional[int] = Field(default=None, description="页码")
    chunk_id: str = Field(description="块ID")
    image_key: Optional[str] = Field(default=None, description="图片key")

class SSEEvent(BaseModel):
    event_type: str = Field(description="事件类型：token/cite/done/error")
    data: Union[str, CiteInfo, Dict[str, Any]] = Field(description="事件数据")

class ReviewRequest(BaseModel):
    query: str = Field(description="综述查询")
    paper_ids: Optional[List[str]] = Field(default=None, description="指定论文ID列表")
    max_papers: int = Field(default=10, description="最大检索论文数")