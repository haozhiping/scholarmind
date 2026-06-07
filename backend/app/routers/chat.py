"""
Chat router — real implementation.
Conversations/messages in PostgreSQL; /query streams via the chat agent (SSE).
"""
import json
from typing import List

from fastapi import APIRouter, Depends, status
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.schemas.chat import (
    ChatQueryRequest,
    ConversationCreate,
    ConversationResponse,
    FeedbackRequest,
    FeedbackResponse,
    MessageResponse,
)
from common.auth import get_current_user_id
from common.db.mysql import get_db_session
from common.logging import logger
from services.chat_agent import memory
from services.chat_agent.agent import run_chat

router = APIRouter(prefix="/chat", tags=["chat"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED,
             summary="新建对话")
async def create_conversation(data: ConversationCreate, user_id: int = Depends(get_current_user_id)):
    conv_id = await memory.get_or_create_conversation(user_id, title=data.title or "新会话")
    convs = await memory.list_conversations(user_id)
    row = next((c for c in convs if c["id"] == conv_id), None)
    return ConversationResponse(
        id=conv_id,
        title=data.title or "新会话",
        folder_id=data.folder_id,
        paper_ids=data.paper_ids or [],
        created_at=row["created_at"] if row else __import__("datetime").datetime.now(),
        updated_at=row["updated_at"] if row else __import__("datetime").datetime.now(),
    )


@router.get("/conversations", response_model=List[ConversationResponse], summary="对话列表")
async def list_conversations(user_id: int = Depends(get_current_user_id)):
    convs = await memory.list_conversations(user_id)
    return [
        ConversationResponse(
            id=c["id"], title=c["title"] or "新会话", folder_id=None, paper_ids=[],
            created_at=c["created_at"], updated_at=c["updated_at"],
        )
        for c in convs
    ]


@router.get("/conversations/{id}/messages", response_model=List[MessageResponse], summary="对话历史消息")
async def list_messages(id: int, user_id: int = Depends(get_current_user_id)):
    msgs = await memory.get_messages(id, user_id)
    out = []
    for m in msgs:
        out.append(MessageResponse(
            id=m["id"],
            conversation_id=m["conversation_id"],
            role=m["role"],
            content=m["content"],
            citations=m.get("citations") or [],
            created_at=m["created_at"],
        ))
    return out


@router.post("/query", summary="论文问答（SSE 流式）",
             description="意图路由 → 改写/翻译/HyDE → 混合检索 → 重排 → 带角标流式作答。")
async def chat_query(request: ChatQueryRequest, user_id: int = Depends(get_current_user_id)):
    conversation_id = request.conversation_id
    if not conversation_id:
        conversation_id = await memory.get_or_create_conversation(user_id, title=request.question[:40])

    async def sse_generator():
        # surface the (possibly newly created) conversation id to the client first
        yield _sse("meta", {"conversation_id": conversation_id})
        try:
            async for ev in run_chat(
                request.question, user_id, conversation_id,
                scope_type=request.scope_type, folder_id=request.folder_id, paper_ids=request.paper_ids,
            ):
                yield _sse(ev["event"], ev["data"])
        except Exception as e:  # noqa: BLE001
            logger.error(f"[chat] query failed: {e}")
            yield _sse("error", {"msg": str(e)})
            yield _sse("done", {"latency_ms": 0})

    return StreamingResponse(sse_generator(), media_type="text/event-stream")


@router.post("/feedback", response_model=FeedbackResponse, summary="答案反馈（点赞/踩）")
async def message_feedback(data: FeedbackRequest, user_id: int = Depends(get_current_user_id)):
    try:
        async with get_db_session() as db:
            await db.execute(
                text("""
                    UPDATE query_logs SET feedback = :fb
                    WHERE id = (SELECT id FROM (
                        SELECT id FROM query_logs WHERE user_id = :u ORDER BY id DESC LIMIT 1
                    ) AS t)
                """),
                {"fb": 1 if data.is_positive else -1, "u": user_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[chat] feedback write failed: {e}")
    return FeedbackResponse(status="success", message="反馈已记录，感谢！")
