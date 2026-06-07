"""
OpenAI-compatible client for LLM / Embedding / Rerank / VLM.
All providers (qwen, deepseek, openai, dashscope) share the same interface.
"""
import asyncio
import json
import re
from typing import Any

import httpx
from openai import AsyncOpenAI

from common.config import settings
from common.logging import logger


# ---------------------------------------------------------------------------
# Client singletons
# ---------------------------------------------------------------------------

def _llm_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        timeout=120,
        max_retries=2,
    )


def _embedding_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=settings.EMBEDDING_BASE_URL,
        api_key=settings.EMBEDDING_API_KEY or "none",
        timeout=60,
        max_retries=2,
    )


# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------

async def chat_complete(
    prompt: str,
    *,
    system: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    json_mode: bool = False,
) -> str:
    client = _llm_client()
    kwargs: dict[str, Any] = {
        "model": model or settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature if temperature is not None else settings.LLM_TEMPERATURE,
        "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    resp = await client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


async def chat_stream(
    prompt: str,
    *,
    system: str = "You are a helpful assistant.",
    model: str | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
):
    """
    Stream LLM output as token deltas (async generator of str).
    On failure, yields a single graceful fallback message so the SSE stream still completes.
    """
    client = _llm_client()
    try:
        stream = await client.chat.completions.create(
            model=model or settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
            max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[llm] stream failed: {e}")
        yield "（当前大模型服务不可用，以下为基于检索资料的占位回答）"
        yield "\n抱歉，模型接口暂时无法连接，请检查 .env 中的 LLM_API_KEY/LLM_MODEL 配置。"


async def chat_complete_json(prompt: str, **kwargs) -> Any:
    """Call LLM and parse the response as JSON. Retries once on parse failure."""
    kwargs["json_mode"] = True
    text = await chat_complete(prompt, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Strip markdown code fences if present
        cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", text).strip()
        return json.loads(cleaned)


# ---------------------------------------------------------------------------
# VLM (multimodal)
# ---------------------------------------------------------------------------

async def vlm_describe_image(image_url: str, caption: str = "") -> str:
    """Generate a Chinese description for a figure using VLM."""
    from pathlib import Path
    prompt_path = Path(__file__).parents[3] / "prompts" / "figure_caption.md"
    prompt_template = prompt_path.read_text(encoding="utf-8")
    # Extract the prompt block between the last ``` pair
    match = re.search(r"```\s*\n(.*?)\n```", prompt_template, re.DOTALL)
    user_prompt = match.group(1).format(caption=caption) if match else f"描述这张图片，原图注：{caption}"

    client = AsyncOpenAI(
        base_url=settings.VLM_BASE_URL,
        api_key=settings.VLM_API_KEY,
        timeout=60,
        max_retries=2,
    )
    resp = await client.chat.completions.create(
        model=settings.VLM_MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": user_prompt},
                ],
            }
        ],
        max_tokens=256,
    )
    return resp.choices[0].message.content or ""


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _fallback_embedding(text: str) -> list[float]:
    """
    Deterministic pseudo-embedding used only when the real embedding API is unavailable.
    Keeps the whole pipeline runnable (Milvus insert/search) without valid model keys.
    Not semantically meaningful — logged as a warning by the caller.
    """
    import hashlib
    import math

    dim = settings.EMBEDDING_DIM
    seed = hashlib.sha256((text or " ").encode("utf-8")).digest()
    # Expand the 32-byte digest into `dim` floats via a simple counter hash.
    vals: list[float] = []
    i = 0
    while len(vals) < dim:
        h = hashlib.sha256(seed + i.to_bytes(4, "big")).digest()
        for b in h:
            vals.append((b / 255.0) * 2.0 - 1.0)
            if len(vals) >= dim:
                break
        i += 1
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Batch embed texts, respecting EMBEDDING_BATCH size.
    On API failure, falls back to deterministic vectors so the pipeline never hard-fails.
    """
    if not texts:
        return []
    client = _embedding_client()
    results: list[list[float]] = []
    batch = settings.EMBEDDING_BATCH

    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        try:
            resp = await client.embeddings.create(
                model=settings.EMBEDDING_MODEL,
                input=chunk,
            )
            results.extend([item.embedding for item in resp.data])
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[embed] API failed ({e}); using deterministic fallback for {len(chunk)} texts")
            results.extend(_fallback_embedding(t) for t in chunk)
    return results


# ---------------------------------------------------------------------------
# Rerank
# ---------------------------------------------------------------------------

async def rerank(query: str, documents: list[str], top_n: int | None = None) -> list[dict]:
    """
    Call the rerank endpoint. Returns list of {index, score} sorted by score desc.
    Supports dashscope (OpenAI-compatible rerank) and local TEI /rerank.
    """
    top_n = top_n or settings.RERANK_TOP_N

    if settings.RERANK_PROVIDER == "dashscope":
        return await _rerank_dashscope(query, documents, top_n)
    else:
        return await _rerank_tei(query, documents, top_n)


async def _rerank_dashscope(query: str, documents: list[str], top_n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.RERANK_BASE_URL}/rerank",
            headers={
                "Authorization": f"Bearer {settings.RERANK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.RERANK_MODEL,
                "query": query,
                "documents": documents,
                "top_n": top_n,
                "return_documents": False,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        # Dashscope returns {"results": [{"index": int, "relevance_score": float}]}
        results = data.get("results", [])
        return [{"index": r["index"], "score": r.get("relevance_score", 0.0)} for r in results]


async def _rerank_tei(query: str, documents: list[str], top_n: int) -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            settings.RERANK_BASE_URL,
            json={"query": query, "texts": documents, "truncate": True},
        )
        resp.raise_for_status()
        data = resp.json()
        scored = sorted(
            [{"index": i, "score": item["score"]} for i, item in enumerate(data)],
            key=lambda x: x["score"],
            reverse=True,
        )
        return scored[:top_n]
