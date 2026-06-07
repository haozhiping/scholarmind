"""
Rerank + Corrective RAG grading.

  - rerank_chunks: calls the rerank API to reorder, keeping top_n. If ENABLE_RERANK is off
    or the API fails, returns the first top_n unchanged.
  - corrective_grade: (ENABLE_CORRECTIVE_RAG only) grades whether the retrieved context can
    answer the question; returns the grade dict so the caller can decide to answer / retry / reject.
"""
from __future__ import annotations

from common.clients.llm import chat_complete_json, rerank
from common.config import settings
from common.logging import logger
from common.prompts import render


async def rerank_chunks(question: str, chunks: list[dict], top_n: int | None = None) -> list[dict]:
    top_n = top_n or settings.RERANK_TOP_N
    if not chunks:
        return []
    if not settings.ENABLE_RERANK:
        return chunks[:top_n]

    documents = [(c.get("content_en") or c.get("content_zh") or "") for c in chunks]
    try:
        ranked = await rerank(question, documents, top_n=top_n)
        out = []
        for r in ranked:
            idx = r["index"]
            if 0 <= idx < len(chunks):
                item = dict(chunks[idx])
                item["rerank_score"] = r.get("score")
                out.append(item)
        return out or chunks[:top_n]
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[rerank] failed, returning top_n unranked: {e}")
        return chunks[:top_n]


def _format_context(chunks: list[dict]) -> str:
    lines = []
    for i, c in enumerate(chunks, 1):
        body = (c.get("content_en") or c.get("content_zh") or "")[:500]
        lines.append(f"[{i}] {body}")
    return "\n".join(lines)


async def corrective_grade(question: str, chunks: list[dict]) -> dict:
    """Return {grade, reason, action}. Defaults to 'answer' when grading is disabled/fails."""
    if not settings.ENABLE_CORRECTIVE_RAG or not chunks:
        return {"grade": "sufficient", "reason": "grading disabled", "action": "answer"}
    try:
        prompt = render("corrective_grade", question=question, context=_format_context(chunks))
        result = await chat_complete_json(prompt, system="你是检索质量评审员。")
        return {
            "grade": result.get("grade", "sufficient"),
            "reason": result.get("reason", ""),
            "action": result.get("action", "answer"),
        }
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[corrective] grading failed: {e}")
        return {"grade": "sufficient", "reason": "grade error", "action": "answer"}
