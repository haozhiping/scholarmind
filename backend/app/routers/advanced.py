"""
Advanced router — Agentic review (SSE) + citation graph (from MySQL citations).
"""
import json
from typing import Optional

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from app.schemas.advanced import (
    CitationEdge,
    CitationGraphResponse,
    CitationNode,
    ReviewGenerateRequest,
)
from common.auth import get_current_user_id
from common.db.mysql import get_db_session
from common.logging import logger
from services.chat_agent.reviewer import generate_review

router = APIRouter(tags=["advanced"])


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/review/generate", summary="Agentic 文献综述生成（SSE 流式）",
             description="Agent 将 topic 分解为子问题 → 检索 → 汇总生成结构化综述。")
async def review_generate(request: ReviewGenerateRequest, user_id: int = Depends(get_current_user_id)):
    async def gen():
        try:
            async for ev in generate_review(
                request.topic, user_id,
                scope_type=request.scope_type, folder_id=request.folder_id, paper_ids=request.paper_ids,
            ):
                yield _sse(ev["event"], ev["data"])
        except Exception as e:  # noqa: BLE001
            logger.error(f"[review] failed: {e}")
            yield _sse("error", {"msg": str(e)})
            yield _sse("done", {"latency_ms": 0})

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/graph/citations", response_model=CitationGraphResponse, summary="论文引用关系图谱")
async def get_citation_graph(paper_id: Optional[int] = None, user_id: int = Depends(get_current_user_id)):
    async with get_db_session() as db:
        # Library papers as nodes
        if paper_id is not None:
            papers_res = await db.execute(
                text("SELECT id, title, authors, year FROM papers WHERE user_id = :u AND id = :p"),
                {"u": user_id, "p": paper_id},
            )
        else:
            papers_res = await db.execute(
                text("SELECT id, title, authors, year FROM papers WHERE user_id = :u"),
                {"u": user_id},
            )
        paper_rows = list(papers_res.mappings().all())
        paper_ids = [r["id"] for r in paper_rows]

        nodes: list[CitationNode] = []
        node_ids: set[int] = set()
        for r in paper_rows:
            authors = r.get("authors")
            if isinstance(authors, str):
                try:
                    authors = ", ".join(json.loads(authors))
                except Exception:
                    pass
            elif isinstance(authors, list):
                authors = ", ".join(str(a) for a in authors)
            nodes.append(CitationNode(id=r["id"], title=r["title"], authors=authors or None, year=r.get("year")))
            node_ids.add(r["id"])

        edges: list[CitationEdge] = []
        if paper_ids:
            id_list = ", ".join(str(i) for i in paper_ids)
            cit_res = await db.execute(
                text(f"SELECT src_paper_id, dst_paper_id, dst_title FROM citations WHERE src_paper_id IN ({id_list})")
            )
            synthetic_id = -1
            title_to_id: dict[str, int] = {}
            for c in cit_res.mappings().all():
                src = c["src_paper_id"]
                dst = c["dst_paper_id"]
                if dst is None:
                    title = (c["dst_title"] or "").strip()
                    if not title:
                        continue
                    if title not in title_to_id:
                        title_to_id[title] = synthetic_id
                        nodes.append(CitationNode(id=synthetic_id, title=title[:200], authors=None, year=None))
                        synthetic_id -= 1
                    dst = title_to_id[title]
                edges.append(CitationEdge(source=src, target=dst, type="reference"))

    return CitationGraphResponse(nodes=nodes, edges=edges)
