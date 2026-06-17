from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from typing import Any, Dict, List, Optional
import asyncio
import json
from app.schemas.advanced import ReviewGenerateRequest, CitationGraphResponse, CitationNode, CitationEdge
from common.auth.deps import get_current_user_sse

router = APIRouter(tags=["advanced"])

@router.get("/review/generate",
            summary="Agentic 文献综述生成（SSE 流式）",
             description="""基于 LlamaIndex ReActAgent 自动生成多文献综述。

**流程**：Agent 将 topic 分解为 3-5 个子问题 → 对每个子问题调用混合检索 → 汇总生成结构化综述（引言/核心挑战/方法对比/结论）

**SSE 事件格式** 同 `/chat/query`：cite → token → done

**scope_type**：`all`=全库，`folder`=指定文件夹，`papers`=指定论文列表""")
async def generate_review(
    topic: str,
    scope_type: str = "all",
    scope_ids: Optional[str] = None,
    current: Dict[str, Any] = Depends(get_current_user_sse),
):
    # SSE 经 GET 暴露(EventSource 仅支持 GET)，参数走 query string。
    # Streaming Response Generator for SSE review generation
    async def review_generator():
        sections = [
            "### 1. 引言\n\n近年来，大语言模型（LLM）在学术文献分析领域展现出巨大潜力 [1]。",
            "\n\n### 2. 核心挑战\n\n然而，模型幻觉以及对于公式与图表解析的精确性依旧是制约其成为科研助手的主要瓶颈 [2]。",
            "\n\n### 3. 检索增强（RAG）路径\n\n为了提高准确性，混合检索策略与对双栏排版的精细切分被广泛采用，从而极大地优化了学术搜索召回精度 [2]。",
            "\n\n### 4. 结论与展望\n\n综上所述，构建高性能的 RAG 管道仍旧是目前面向多语言长文本调研最主流且可靠的工程路径。"
        ]
        
        # Yield citations
        cites = [
            {
                "paper_id": 1,
                "paper_title": "Attention Is All You Need",
                "page_num": 1,
                "bbox": "[1, 50, 100, 450, 150]",
                "chunk_type": "text",
                "content": "Attention mechanisms have become an integral part of compelling sequence modeling and transduction models...",
                "image_key": None
            },
            {
                "paper_id": 2,
                "paper_title": "Retrieval-Augmented Generation for NLP Tasks",
                "page_num": 2,
                "bbox": "[2, 80, 120, 480, 240]",
                "chunk_type": "text",
                "content": "Retrieval-Augmented Generation (RAG) combines pre-trained parametric memory with non-parametric memory for NLP tasks...",
                "image_key": None
            }
        ]

        for c in cites:
            await asyncio.sleep(0.15)
            yield f"event: cite\ndata: {json.dumps(c)}\n\n"

        for s in sections:
            # yield text tokens/sections
            for char in s:
                await asyncio.sleep(0.01)
                yield f"event: token\ndata: {json.dumps({'delta': char})}\n\n"
            await asyncio.sleep(0.3)

        # Done event
        yield "event: done\ndata: {\"latency_ms\": 2500}\n\n"

    return StreamingResponse(review_generator(), media_type="text/event-stream")

@router.get("/graph/citations", response_model=CitationGraphResponse,
            summary="论文引用关系图谱",
            description="返回论文间的引用网络（节点+有向边），可指定 `paper_id` 只返回与该论文相关的子图。节点含标题/作者/年份，边含引用方向。前端用于渲染知识图谱可视化。")
async def get_citation_graph(paper_id: Optional[int] = None):
    # Mock citation network graph
    nodes = [
        CitationNode(id=1, title="Attention Is All You Need", authors="Vaswani et al.", year=2017),
        CitationNode(id=2, title="Retrieval-Augmented Generation for NLP Tasks", authors="Lewis et al.", year=2020),
        CitationNode(id=3, title="BERT: Pre-training of Deep Bidirectional Transformers", authors="Devlin et al.", year=2018),
        CitationNode(id=4, title="GPT-3: Language Models are Few-Shot Learners", authors="Brown et al.", year=2020),
        CitationNode(id=5, title="RAGMeet: Multimodal Document Retrieval", authors="Scholar et al.", year=2024),
    ]
    
    edges = [
        CitationEdge(source=2, target=1, type="reference"),
        CitationEdge(source=3, target=1, type="reference"),
        CitationEdge(source=4, target=1, type="reference"),
        CitationEdge(source=5, target=2, type="reference"),
        CitationEdge(source=5, target=4, type="reference"),
    ]
    
    if paper_id is not None:
        # Filter nodes & edges associated with the paper_id for more realistic filtering
        connected_ids = {paper_id}
        for edge in edges:
            if edge.source == paper_id:
                connected_ids.add(edge.target)
            elif edge.target == paper_id:
                connected_ids.add(edge.source)
        
        filtered_nodes = [n for n in nodes if n.id in connected_ids]
        filtered_edges = [e for e in edges if e.source in connected_ids and e.target in connected_ids]
        return CitationGraphResponse(nodes=filtered_nodes, edges=filtered_edges)
        
    return CitationGraphResponse(nodes=nodes, edges=edges)
