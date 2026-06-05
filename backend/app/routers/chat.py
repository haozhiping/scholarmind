import json
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from app.schemas.chat import (
    ConversationResponse,
    ConversationCreate,
    MessageResponse,
    FeedbackRequest,
    FeedbackResponse,
    CitationResponse,
)
from common.auth.deps import get_current_user, get_current_user_sse
from common.exceptions import NotFoundException, DatabaseException
from common.logging import logger

# ChatService — lazy singleton, initialised on first request.
from services.chat_agent.chat_service import ChatService
from services.chat_agent.schemas import ChatRequest as AgentChatRequest

_chat_service: Optional[ChatService] = None


def get_chat_service() -> ChatService:
    global _chat_service
    if _chat_service is None:
        _chat_service = ChatService()
    return _chat_service


router = APIRouter(prefix="/chat", tags=["chat"])


# ---------------------------------------------------------------------------
# Helpers — schema ↔ service mapping
# ---------------------------------------------------------------------------

def _conv_to_response(conv) -> ConversationResponse:
    """Convert ChatService Conversation → route ConversationResponse."""
    return ConversationResponse(
        id=str(conv.conv_id),
        title=conv.title or "",
        folder_id=getattr(conv, "folder_id", None),
        paper_ids=getattr(conv, "paper_ids", None) or [],
        created_at=conv.created_at,
        updated_at=getattr(conv, "updated_at", conv.created_at),
    )


def _msg_to_response(msg: Dict[str, Any]) -> MessageResponse:
    """Convert ChatService message dict → route MessageResponse."""
    cites = []
    for c in msg.get("citations") or []:
        cites.append(CitationResponse(
            paper_id=c.get("paper_id", 0),
            paper_title=c.get("paper_title", ""),
            page_num=c.get("page_num") or c.get("page", 0),
            bbox=c.get("bbox", ""),
            chunk_type=c.get("chunk_type", "text"),
            content=c.get("content", ""),
            image_key=c.get("image_key"),
        ))
    return MessageResponse(
        id=msg["id"],
        conversation_id=msg["conversation_id"],
        role=msg["role"],
        content=msg["content"],
        citations=cites if cites else None,
        created_at=msg["created_at"],
    )


def _user_id_from_auth(current: Dict[str, Any]) -> str:
    """Extract user_id string from auth dict (id may be int or str)."""
    return str(current.get("id", ""))


# ---------------------------------------------------------------------------
# 1. POST /conversations — create
# ---------------------------------------------------------------------------

@router.post(
    "/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="新建对话",
    description="创建一个新的对话会话。后续提问时带上 conversation_id 即可携带上下文记忆。",
)
async def create_conversation(
    data: ConversationCreate,
    current: Dict[str, Any] = Depends(get_current_user),
):
    svc = get_chat_service()
    try:
        conv = await svc.create_conversation(user_id=_user_id_from_auth(current))
        return _conv_to_response(conv)
    except DatabaseException as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 2. GET /conversations — list
# ---------------------------------------------------------------------------

@router.get(
    "/conversations",
    response_model=List[ConversationResponse],
    summary="对话列表",
    description="获取当前用户的所有对话会话，按更新时间倒序排列。",
)
async def list_conversations(
    current: Dict[str, Any] = Depends(get_current_user),
):
    svc = get_chat_service()
    try:
        convs = await svc.get_conversations(user_id=_user_id_from_auth(current))
        return [_conv_to_response(c) for c in convs]
    except DatabaseException as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 3. GET /conversations/{id}/messages — message history
# ---------------------------------------------------------------------------

@router.get(
    "/conversations/{id}/messages",
    response_model=List[MessageResponse],
    summary="对话历史消息",
    description="获取指定会话的完整消息历史。",
)
async def list_messages(
    id: str,
    current: Dict[str, Any] = Depends(get_current_user),
):
    svc = get_chat_service()
    try:
        msgs = await svc.get_messages(conv_id=id)
        return [_msg_to_response(m) for m in msgs]
    except NotFoundException as e:
        raise HTTPException(status_code=404, detail=str(e))
    except DatabaseException as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# 4. GET /query — SSE streaming chat
# ---------------------------------------------------------------------------

@router.get(
    "/query",
    summary="论文问答（SSE 流式）",
    description="""向已入库的论文提问，SSE 流式返回答案和引用。

**流程**：意图路由 → 查询改写+翻译+HyDE → 混合检索（dense+sparse）→ RRF 融合 → Reranker 重排 → LLM 生成带角标答案

**SSE 事件格式**：
- `event: cite` — 引用块（paper_id / page_num / bbox / content / image_key）
- `event: token` — 流式文字 delta
- `event: done` — 结束，含 latency_ms

**scope_type**：`all`=全库，`folder`=指定文件夹，`papers`=指定论文列表""",
)
async def chat_query(
    conversation_id: str,
    question: str,
    scope_type: str = "all",
    scope_ids: Optional[str] = None,
    current: Dict[str, Any] = Depends(get_current_user_sse),
):
    svc = get_chat_service()
    t0 = time.perf_counter()

    async def sse_generator():
        try:
            request = AgentChatRequest(
                conv_id=conversation_id,
                query=question,
                stream=True,
            )
            async for line in svc.query(request, user_id=_user_id_from_auth(current)):
                # ChatService yields JSON lines: {"event": "...", "data": {...}}
                try:
                    event_obj = json.loads(line)
                except json.JSONDecodeError:
                    # raw text fallback
                    yield f"event: token\ndata: {json.dumps({'delta': line})}\n\n"
                    continue

                event_type = event_obj.get("event", "token")
                data = event_obj.get("data", {})

                # Map ChatService data → frontend-expected format
                if event_type == "token":
                    # ChatService uses "content", frontend expects "delta"
                    text = data.get("content") or data.get("delta") or ""
                    yield f"event: token\ndata: {json.dumps({'delta': text})}\n\n"

                elif event_type == "cite":
                    yield f"event: cite\ndata: {json.dumps(data)}\n\n"

                elif event_type == "done":
                    latency_ms = int((time.perf_counter() - t0) * 1000)
                    done_data = data.copy()
                    done_data["latency_ms"] = latency_ms
                    yield f"event: done\ndata: {json.dumps(done_data)}\n\n"

                elif event_type == "error":
                    logger.error(f"Chat query error: {data}")
                    yield f"event: error\ndata: {json.dumps(data)}\n\n"

                else:
                    # Unknown event → forward as-is
                    yield f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

        except Exception as e:
            logger.error(f"SSE generator error: {e}")
            yield f"event: error\ndata: {json.dumps({'message': str(e)})}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# 5. POST /feedback — save feedback
# ---------------------------------------------------------------------------

@router.post(
    "/feedback",
    response_model=FeedbackResponse,
    summary="答案反馈（点赞/踩）",
    description="对 assistant 回答进行正负反馈，数据写入 query_logs。is_positive=true 为点赞，false 为踩，可附带原因说明。",
)
async def message_feedback(
    data: FeedbackRequest,
    current: Dict[str, Any] = Depends(get_current_user),
):
    svc = get_chat_service()
    try:
        await svc.save_feedback(
            message_id=data.message_id,
            is_positive=data.is_positive,
            reason=data.reason or "",
            user_id=_user_id_from_auth(current),
        )
        return FeedbackResponse(
            status="success",
            message="Feedback saved successfully. Thank you for your feedback!",
        )
    except DatabaseException as e:
        raise HTTPException(status_code=500, detail=str(e))
