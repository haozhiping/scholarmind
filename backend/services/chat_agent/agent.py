"""
Chat agent orchestration -> stream of SSE-ready events.

Flow per /api/chat/query:
  1. load conversation history (PostgreSQL)
  2. intent routing (chitchat -> direct LLM, no retrieval; else RAG)
  3. RAG: retrieve -> build numbered context + citations
  4. stream answer (answer_with_citation) as token events
  5. persist user + assistant messages (after stream) + query_logs

Events yielded: {"event": "cite"|"token"|"done"|"error", "data": {...}}
"""
from __future__ import annotations

import json
import time
from typing import AsyncGenerator, Optional

from sqlalchemy import text

from common.clients.llm import chat_complete_json, chat_stream
from common.config import settings
from common.db.mysql import get_db_session
from common.logging import logger
from common.prompts import render
from services.chat_agent import memory
from services.retrieval import retrieve


async def _paper_titles(paper_ids: list[int]) -> dict[int, str]:
    ids = [int(p) for p in {pid for pid in paper_ids if pid}]
    if not ids:
        return {}
    try:
        id_list = ", ".join(str(i) for i in ids)  # ints only -> safe to inline
        async with get_db_session() as db:
            res = await db.execute(text(f"SELECT id, title FROM papers WHERE id IN ({id_list})"))
            return {r["id"]: r["title"] for r in res.mappings().all()}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[agent] title lookup failed: {e}")
        return {}


def _build_citation(idx: int, chunk: dict, title: str) -> dict:
    return {
        "paper_id": chunk.get("paper_id") or 0,
        "paper_title": title or f"论文 {chunk.get('paper_id')}",
        "page_num": chunk.get("page_num") if (chunk.get("page_num") or -1) >= 0 else 0,
        "bbox": chunk.get("bbox") or "",
        "chunk_type": chunk.get("chunk_type") or "text",
        "content": chunk.get("content_en") or chunk.get("content_zh") or "",
        "image_key": chunk.get("image_key") or None,
    }


def _format_context(chunks: list[dict], titles: dict[int, str]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        title = titles.get(c.get("paper_id"), "")
        body = (c.get("content_zh") or c.get("content_en") or "")[:600]
        page = c.get("page_num")
        loc = f"P.{page}" if (page or -1) >= 0 else ""
        lines.append(f"[{i}] 《{title}》{loc}\n{body}")
    return "\n\n".join(lines)


async def _intent(question: str, history: list[dict]) -> dict:
    if not settings.ENABLE_INTENT_ROUTER:
        return {"intent": "knowledge", "need_retrieval": True}
    try:
        hist_str = "\n".join(f"{h['role']}: {h['content']}" for h in history[-4:]) or "（无）"
        result = await chat_complete_json(
            render("intent_router", question=question, history=hist_str),
            system="你是意图路由器。",
        )
        return {
            "intent": result.get("intent", "knowledge"),
            "need_retrieval": result.get("need_retrieval", True),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[agent] intent routing failed, defaulting to knowledge: {e}")
        return {"intent": "knowledge", "need_retrieval": True}


async def run_chat(
    question: str,
    user_id: int,
    conversation_id: int,
    *,
    scope_type: str = "all",
    folder_id: Optional[int] = None,
    paper_ids: Optional[list[int]] = None,
) -> AsyncGenerator[dict, None]:
    t0 = time.monotonic()
    history = await memory.get_history(conversation_id, limit=10)

    # Resolve scope
    eff_folder = folder_id if scope_type == "folder" else None
    eff_papers = paper_ids if scope_type == "papers" else None

    intent = await _intent(question, history)
    chunks: list[dict] = []
    citations: list[dict] = []

    if intent["need_retrieval"]:
        try:
            chunks = await retrieve(
                question, user_id,
                folder_id=eff_folder, paper_ids=eff_papers, history=history,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[agent] retrieve failed: {e}")
            chunks = []

        titles = await _paper_titles([c.get("paper_id") for c in chunks if c.get("paper_id")])
        for i, c in enumerate(chunks, 1):
            cite = _build_citation(i, c, titles.get(c.get("paper_id"), ""))
            citations.append(cite)
            yield {"event": "cite", "data": cite}

        context = _format_context(chunks, titles)
        hist_str = "\n".join(f"{h['role']}: {h['content']}" for h in history[-6:]) or "（无）"
        prompt = render("answer_with_citation", question=question, context=context or "（未检索到相关资料）", history=hist_str)
        system = "你是严谨的科研助手，只能基于参考资料用中文作答并标注角标。"
    else:
        # Chitchat: answer directly without retrieval
        prompt = f"用中文友好简洁地回答用户：{question}"
        system = "你是学术助手文渊，可以闲聊但保持专业。"

    # Stream the answer
    answer_parts: list[str] = []
    try:
        async for delta in chat_stream(prompt, system=system):
            answer_parts.append(delta)
            yield {"event": "token", "data": {"delta": delta}}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[agent] generation failed: {e}")
        yield {"event": "error", "data": {"msg": str(e)}}

    answer = "".join(answer_parts)
    latency_ms = int((time.monotonic() - t0) * 1000)

    # Persist (after stream completes, to avoid truncated writes)
    try:
        await memory.save_message(conversation_id, "user", question)
        await memory.save_message(conversation_id, "assistant", answer, citations)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[agent] persist message failed: {e}")

    await _write_query_log(user_id, conversation_id, question, chunks, latency_ms, answer)

    yield {"event": "done", "data": {"latency_ms": latency_ms}}


async def _write_query_log(user_id, conversation_id, question, chunks, latency_ms, answer):
    try:
        chunk_ids = [c.get("id") for c in chunks if c.get("id")]
        async with get_db_session() as db:
            await db.execute(
                text("""
                    INSERT INTO query_logs
                        (user_id, conversation_id, question, retrieved_chunk_ids, top_k, latency_ms,
                         prompt_tokens, completion_tokens)
                    VALUES (:u, :c, :q, CAST(:ids AS JSON), :k, :lat, :pt, :ct)
                """),
                {
                    "u": user_id,
                    "c": conversation_id,
                    "q": question,
                    "ids": json.dumps(chunk_ids, ensure_ascii=False),
                    "k": len(chunks),
                    "lat": latency_ms,
                    "pt": None,
                    "ct": len(answer) // 2 if answer else 0,
                },
            )
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[agent] query_log write failed: {e}")
