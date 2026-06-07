"""
Bilingual enrichment: fill chunk.content_zh for cross-lingual recall.

  - text chunks: LLM generates a Chinese summary + keywords (prompts/enrich_zh_summary).
  - figure chunks: content_zh already set by the VLM in the parsing stage (kept as-is).
  - table/formula/non-English or LLM-failure: content_zh falls back to content_en.

Batched with bounded concurrency (8) to avoid hammering the model endpoint.
"""
from __future__ import annotations

import asyncio
import json

from common.clients.llm import chat_complete_json
from common.logging import logger
from common.prompts import render
from services.indexing.chunker import Chunk

_CONCURRENCY = 8


async def _enrich_one(chunk: Chunk, sem: asyncio.Semaphore) -> None:
    # figure: keep VLM description; if empty, fall back to caption
    if chunk.block_type == "figure":
        if not chunk.content_zh:
            chunk.content_zh = chunk.content_en
        return
    # table/formula: reuse original content for the zh field
    if chunk.block_type in ("table", "formula"):
        chunk.content_zh = chunk.content_en
        return

    async with sem:
        try:
            prompt = render("enrich_zh_summary", section=chunk.section or "正文", chunk_text=chunk.content_en[:3000])
            result = await chat_complete_json(prompt, system="你是中文学术摘要助手。")
            summary = (result or {}).get("summary_zh", "")
            keywords = (result or {}).get("keywords_zh", [])
            kw = " ".join(keywords) if isinstance(keywords, list) else str(keywords)
            chunk.content_zh = (summary + " " + kw).strip() or chunk.content_en
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[enrich] LLM failed, fallback to content_en: {e}")
            chunk.content_zh = chunk.content_en


async def enrich_chunks(chunks: list[Chunk]) -> list[Chunk]:
    sem = asyncio.Semaphore(_CONCURRENCY)
    await asyncio.gather(*[_enrich_one(c, sem) for c in chunks])
    return chunks
