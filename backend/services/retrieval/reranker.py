"""Reranker — post-retrieval re-ranking + Corrective RAG quality gate."""
from typing import Optional

from common.clients.llm import rerank, chat_complete
from common.config import settings
from common.logging import logger

from .searcher import ScoredChunk


async def rerank_chunks(
    question: str,
    chunks: list[ScoredChunk],
    top_n: Optional[int] = None,
) -> list[ScoredChunk]:
    """Re-rank chunks using the configured reranker model.
    
    When ENABLE_RERANK is False, returns top_n chunks by existing score.
    """
    top_n = top_n or settings.RERANK_TOP_N

    if not chunks:
        return []

    if not settings.ENABLE_RERANK:
        return chunks[:top_n]

    if len(chunks) == 1:
        return chunks

    # Prepare texts for reranking — prefer content_zh for Chinese queries,
    # fall back to content_en
    texts = [
        ch.content_zh if ch.content_zh else ch.content_en
        for ch in chunks
    ]

    try:
        scores = await rerank(question, texts, top_n=top_n)
    except Exception as e:
        logger.error(f"Reranker failed, falling back to original order: {e}")
        return chunks[:top_n]

    # Merge rerank results back
    reranked: list[ScoredChunk] = []
    for item in scores:
        idx = item["index"]
        if idx < len(chunks):
            ch = chunks[idx]
            ch.score = item.get("score", ch.score)
            reranked.append(ch)

    logger.info(f"Reranked {len(chunks)} → {len(reranked)} chunks")
    return reranked


_CORRECTIVE_GRADE_SYSTEM = (
    "You are a quality evaluator for academic retrieval. "
    "For each document chunk below, rate its relevance to the given question "
    "on a scale of 0.0 (irrelevant) to 1.0 (perfectly relevant). "
    "Output a strict JSON array of scores, one per chunk, in order. "
    "Example: [0.9, 0.3, 0.7]"
)


async def corrective_grade(
    question: str,
    chunks: list[ScoredChunk],
    threshold: float = 0.5,
) -> list[ScoredChunk]:
    """Corrective RAG: grade chunk relevance and filter low-quality results.

    If >= 3 chunks pass threshold, return them. Otherwise return empty
    (caller should trigger query rewrite or refuse to answer).
    
    Only active when ENABLE_CORRECTIVE_RAG is True.
    """
    if not settings.ENABLE_CORRECTIVE_RAG or not chunks:
        return chunks

    if len(chunks) <= 3:
        return chunks

    texts = [ch.content_en for ch in chunks]

    prompt = (
        f"Question: {question}\n\n"
        "Chunks:\n" +
        "\n---\n".join(f"[{i}] {t[:500]}" for i, t in enumerate(texts))
    )

    try:
        import json
        response = await chat_complete(
            prompt=prompt,
            system=_CORRECTIVE_GRADE_SYSTEM,
            temperature=0.0,
            max_tokens=256,
            json_mode=True,
        )
        grades = json.loads(response)
    except Exception as e:
        logger.warning(f"Corrective RAG grading failed: {e}")
        return chunks

    # Normalize: ensure grade list length matches
    if not isinstance(grades, list) or len(grades) != len(chunks):
        logger.warning("Corrective RAG: unexpected grade format, skipping")
        return chunks

    graded = [
        (ch, float(g))
        for ch, g in zip(chunks, grades)
        if isinstance(g, (int, float)) and float(g) >= threshold
    ]

    # Sort by grade descending
    graded.sort(key=lambda x: x[1], reverse=True)
    result = [ch for ch, _ in graded]

    # Below threshold: if not enough quality chunks, signal caller to rewrite/refuse
    if len(result) < 3:
        logger.info(f"Corrective RAG: only {len(result)} chunks above threshold {threshold}")
        # Return only high-quality ones (may be 0-2); caller decides next action

    return result


async def evaluate_and_filter(
    question: str,
    chunks: list[ScoredChunk],
    top_n: Optional[int] = None,
) -> tuple[list[ScoredChunk], str]:
    """Full post-retrieval pipeline: rerank + corrective grade.
    
    Returns:
        (filtered_chunks, status): status is "ok" | "low_quality" | "empty"
    """
    if not chunks:
        return [], "empty"

    # Step 1: rerank
    reranked = await rerank_chunks(question, chunks, top_n)

    # Step 2: corrective grade (if enabled)
    filtered = await corrective_grade(question, reranked)

    if not filtered:
        return [], "empty"
    if len(filtered) < 3:
        return filtered, "low_quality"
    return filtered, "ok"
