from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from typing import List, Optional
from datetime import datetime
import asyncio
import json
from app.schemas.chat import (
    ConversationResponse, ConversationCreate, MessageResponse, 
    ChatQueryRequest, FeedbackRequest, FeedbackResponse, CitationResponse
)

router = APIRouter(prefix="/chat", tags=["chat"])

# Mock In-Memory Database
MOCK_CONVERSATIONS = [
    ConversationResponse(
        id=101,
        title="Attention 与 Transformer 结构探讨",
        folder_id=1,
        paper_ids=[1],
        created_at=datetime.now(),
        updated_at=datetime.now()
    ),
    ConversationResponse(
        id=102,
        title="RAG 混合检索及优化路线",
        folder_id=3,
        paper_ids=[2],
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
]

MOCK_MESSAGES = {
    101: [
        MessageResponse(
            id=1001,
            conversation_id=101,
            role="user",
            content="什么是 Transformer 架构的核心？",
            citations=[],
            created_at=datetime.now()
        ),
        MessageResponse(
            id=1002,
            conversation_id=101,
            role="assistant",
            content="Transformer 架构的核心是自注意力机制（Self-Attention），它允许模型在处理序列中的每个位置时，计算该位置与序列中所有其他位置的相关性，从而建立全局依赖关系。",
            citations=[
                CitationResponse(
                    paper_id=1,
                    paper_title="Attention Is All You Need",
                    page_num=3,
                    bbox="[3, 100, 150, 480, 280]",
                    chunk_type="text",
                    content="We propose the Transformer, a model architecture eschewing recurrence and instead relying entirely on an attention mechanism...",
                    image_key=None
                )
            ],
            created_at=datetime.now()
        )
    ],
    102: []
}

@router.post("/conversations", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED,
             summary="新建对话",
             description="创建一个新的对话会话，可绑定文件夹或指定论文范围（`paper_ids`）。后续提问时带上 `conversation_id` 即可携带上下文记忆。")
async def create_conversation(data: ConversationCreate):
    new_id = len(MOCK_CONVERSATIONS) + 101
    title = data.title or f"新会话 {new_id}"
    new_conv = ConversationResponse(
        id=new_id,
        title=title,
        folder_id=data.folder_id,
        paper_ids=data.paper_ids or [],
        created_at=datetime.now(),
        updated_at=datetime.now()
    )
    MOCK_CONVERSATIONS.append(new_conv)
    MOCK_MESSAGES[new_id] = []
    return new_conv

@router.get("/conversations", response_model=List[ConversationResponse],
            summary="对话列表",
            description="获取当前用户的所有对话会话，按更新时间倒序排列。")
async def list_conversations():
    return MOCK_CONVERSATIONS

@router.get("/conversations/{id}/messages", response_model=List[MessageResponse],
            summary="对话历史消息",
            description="获取指定会话的完整消息历史，每条 assistant 消息包含引用溯源信息（`citations`：论文ID、页码、bbox 坐标、原文片段）。")
async def list_messages(id: int):
    if id in MOCK_MESSAGES:
        return MOCK_MESSAGES[id]
    return []

@router.get("/query",
            summary="论文问答（SSE 流式）",
             description="""向已入库的论文提问，SSE 流式返回答案和引用。

**流程**：意图路由 → 查询改写+翻译+HyDE → 混合检索（dense+sparse）→ RRF 融合 → Reranker 重排 → LLM 生成带角标答案

**SSE 事件格式**：
- `event: cite` — 引用块（paper_id / page_num / bbox / content / image_key）
- `event: token` — 流式文字 delta
- `event: done` — 结束，含 latency_ms

**scope_type**：`all`=全库，`folder`=指定文件夹，`papers`=指定论文列表""")
async def chat_query(
    conversation_id: int,
    question: str,
    scope_type: str = "all",
    scope_ids: Optional[str] = None,
):
    # SSE 必须经 GET 暴露：前端用 EventSource(只支持 GET)。参数走 query string，
    # 与 frontend chatAPI.getQuerySSEUrl 构造的 URL 对齐。
    # Streaming Response Generator for SSE
    async def sse_generator():
        tokens = [
            "根据", "先前", "有关", " RAG ", "的研究", " ", 
            "**Attention Is All You Need**", " [1] ", "中", "提出", "的", 
            " Transformer ", "架构，", "多头", "注意力", "机制", "大大", 
            "增强了", "序列", "特征", "的", "建模", "能力。", 
            "对于", "多文档", "及", "复杂", "对比", "任务，", "通常", 
            "结合", "混合", "检索", " [2] ", "能够", "召回", "更", "精准", "的信息。"
        ]
        
        # 1. Yield citation information first or early
        cites = [
            {
                "paper_id": 1,
                "paper_title": "Attention Is All You Need",
                "page_num": 3,
                "bbox": "[3, 100, 200, 500, 300]",
                "chunk_type": "text",
                "content": "We propose the Transformer, a model architecture eschewing recurrence and instead relying entirely on an attention mechanism to draw global dependencies between input and output.",
                "image_key": None
            },
            {
                "paper_id": 2,
                "paper_title": "Retrieval-Augmented Generation for NLP Tasks",
                "page_num": 5,
                "bbox": "[5, 50, 80, 480, 220]",
                "chunk_type": "table",
                "content": '<table border="1" class="mock-table"><tr><th>Model</th><th>Accuracy</th></tr><tr><td>Dense Retrieve</td><td>44.2%</td></tr><tr><td>Hybrid (RRF)</td><td>51.8%</td></tr></table>',
                "image_key": "fig_dataset_comparison"
            }
        ]
        
        for c in cites:
            await asyncio.sleep(0.1)
            yield f"event: cite\ndata: {json.dumps(c)}\n\n"
            
        # 2. Yield text tokens
        for t in tokens:
            await asyncio.sleep(0.08)
            payload = {"delta": t}
            yield f"event: token\ndata: {json.dumps(payload)}\n\n"
            
        # 3. Yield done event
        await asyncio.sleep(0.1)
        yield "event: done\ndata: {\"latency_ms\": 652}\n\n"

    # Add the query to history mock database
    if conversation_id in MOCK_MESSAGES:
        # Mocking user message adding
        user_msg = MessageResponse(
            id=int(datetime.now().timestamp() * 1000),
            conversation_id=conversation_id,
            role="user",
            content=question,
            citations=[],
            created_at=datetime.now()
        )
        MOCK_MESSAGES[conversation_id].append(user_msg)
        
        # Mocking assistant message adding (will be complete after stream)
        assistant_content = "根据先前有关 RAG 的研究 Attention Is All You Need [1] 中提出的 Transformer 架构，多头注意力机制大大增强了序列特征的建模能力。对于多文档及复杂对比任务，通常结合混合检索 [2] 能够召回更精准的信息。"
        assistant_msg = MessageResponse(
            id=int(datetime.now().timestamp() * 1000) + 1,
            conversation_id=conversation_id,
            role="assistant",
            content=assistant_content,
            citations=[
                CitationResponse(
                    paper_id=1,
                    paper_title="Attention Is All You Need",
                    page_num=3,
                    bbox="[3, 100, 200, 500, 300]",
                    chunk_type="text",
                    content="We propose the Transformer, a model architecture eschewing recurrence and instead relying entirely on an attention mechanism to draw global dependencies between input and output.",
                    image_key=None
                ),
                CitationResponse(
                    paper_id=2,
                    paper_title="Retrieval-Augmented Generation for NLP Tasks",
                    page_num=5,
                    bbox="[5, 50, 80, 480, 220]",
                    chunk_type="table",
                    content='<table border="1" class="mock-table"><tr><th>Model</th><th>Accuracy</th></tr><tr><td>Dense Retrieve</td><td>44.2%</td></tr><tr><td>Hybrid (RRF)</td><td>51.8%</td></tr></table>',
                    image_key="fig_dataset_comparison"
                )
            ],
            created_at=datetime.now()
        )
        MOCK_MESSAGES[conversation_id].append(assistant_msg)

    return StreamingResponse(sse_generator(), media_type="text/event-stream")

@router.post("/feedback", response_model=FeedbackResponse,
             summary="答案反馈（点赞/踩）",
             description="对 assistant 回答进行正负反馈，数据写入 query_logs 用于后续质量分析。`is_positive=true` 为点赞，`false` 为踩，可附带原因说明。")
async def message_feedback(data: FeedbackRequest):
    return FeedbackResponse(
        status="success",
        message="Feedback saved successfully. Thank you for your feedback!"
    )
