"""
Query optimization: rewrite + translate + HyDE, executed concurrently.

Controlled by settings.ENABLE_QUERY_REWRITE / ENABLE_QUERY_TRANSLATION / ENABLE_HYDE.
Each step degrades to the original question on failure.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from common.clients.llm import chat_complete
from common.config import settings
from common.logging import logger
from common.prompts import render


@dataclass
class QueryBundle:
    original: str
    rewritten: str
    translated_en: str
    hyde_doc: str


def _format_history(history: list[dict] | None) -> str:
    if not history:
        return "（无）"
    return "\n".join(f"{h.get('role', 'user')}: {h.get('content', '')}" for h in history[-6:])


async def optimize_query(question: str, conversation_history: list[dict] | None = None) -> QueryBundle:
    history_str = _format_history(conversation_history)

    async def _rewrite() -> str:
        if not settings.ENABLE_QUERY_REWRITE:
            return question
        try:
            out = await chat_complete(render("query_rewrite", question=question, history=history_str),
                                      system="你是检索查询优化器。")
            return out.strip().splitlines()[0] if out.strip() else question
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[query_opt] rewrite failed: {e}")
            return question

    async def _translate() -> str:
        if not settings.ENABLE_QUERY_TRANSLATION:
            return question
        try:
            out = await chat_complete(render("query_translate", question=question),
                                      system="You are a precise translator.")
            return out.strip().splitlines()[0] if out.strip() else question
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[query_opt] translate failed: {e}")
            return question

    async def _hyde() -> str:
        if not settings.ENABLE_HYDE:
            return ""
        try:
            out = await chat_complete(render("hyde", question=question),
                                      system="You are a research assistant.")
            return out.strip()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[query_opt] hyde failed: {e}")
            return ""

    rewritten, translated_en, hyde_doc = await asyncio.gather(_rewrite(), _translate(), _hyde())
    return QueryBundle(
        original=question,
        rewritten=rewritten,
        translated_en=translated_en,
        hyde_doc=hyde_doc,
    )
