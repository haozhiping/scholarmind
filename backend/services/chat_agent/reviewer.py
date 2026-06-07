"""
Agentic literature review generator -> stream of SSE-ready events.

Lightweight agent loop (no heavy framework, keeps the system runnable):
  1. decompose the topic into 3-5 sub-questions (LLM)
  2. retrieve chunks for each sub-question (concurrent)
  3. dedupe + assemble numbered context, emit citations
  4. stream the review (review_generation prompt, reason model)
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import AsyncGenerator, Optional

from common.clients.llm import chat_complete_json, chat_stream
from common.config import settings
from common.db.mysql import get_db_session
from common.logging import logger
from common.prompts import render
from services.chat_agent.agent import _build_citation, _paper_titles
from services.retrieval import retrieve


async def _decompose(topic: str) -> list[str]:
    try:
        prompt = (
            f"将下面的综述主题分解为 3-5 个互补的检索子问题，覆盖背景/方法/对比/趋势。\n"
            f"主题：{topic}\n"
            f'只输出 JSON：{{"subquestions": ["...", "..."]}}'
        )
        result = await chat_complete_json(prompt, system="你是学术综述规划助手。")
        subs = result.get("subquestions", [])
        return [s for s in subs if isinstance(s, str)][:5] or [topic]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[reviewer] decompose failed: {e}")
        return [topic]


async def generate_review(
    topic: str,
    user_id: int,
    *,
    scope_type: str = "all",
    folder_id: Optional[int] = None,
    paper_ids: Optional[list[int]] = None,
) -> AsyncGenerator[dict, None]:
    t0 = time.monotonic()
    eff_folder = folder_id if scope_type == "folder" else None
    eff_papers = paper_ids if scope_type == "papers" else None

    sub_questions = await _decompose(topic)
    logger.info(f"[reviewer] topic={topic!r} sub_questions={sub_questions}")

    # Retrieve per sub-question concurrently
    async def _r(q: str):
        try:
            return await retrieve(q, user_id, folder_id=eff_folder, paper_ids=eff_papers, top_n=4)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[reviewer] retrieve failed for {q!r}: {e}")
            return []

    results = await asyncio.gather(*[_r(q) for q in sub_questions])

    # Dedupe chunks by id
    seen: set = set()
    chunks: list[dict] = []
    for lst in results:
        for c in lst:
            cid = c.get("id")
            if cid and cid not in seen:
                seen.add(cid)
                chunks.append(c)

    titles = await _paper_titles([c.get("paper_id") for c in chunks if c.get("paper_id")])
    for i, c in enumerate(chunks, 1):
        yield {"event": "cite", "data": _build_citation(i, c, titles.get(c.get("paper_id"), ""))}

    # Build context
    ctx_lines = []
    for i, c in enumerate(chunks, 1):
        title = titles.get(c.get("paper_id"), "")
        body = (c.get("content_zh") or c.get("content_en") or "")[:500]
        ctx_lines.append(f"[{i}] 《{title}》\n{body}")
    papers_context = "\n\n".join(ctx_lines) or "（未检索到相关文献）"

    prompt = render(
        "review_generation",
        topic=topic,
        papers_context=papers_context,
        min_citations=max(1, min(3, len(chunks))),
    )
    # Prefer the reasoning model for synthesis quality
    async for delta in chat_stream(prompt, system="你是学术综述撰写助手。", model=settings.LLM_REASON_MODEL):
        yield {"event": "token", "data": {"delta": delta}}

    latency_ms = int((time.monotonic() - t0) * 1000)
    yield {"event": "done", "data": {"latency_ms": latency_ms}}
