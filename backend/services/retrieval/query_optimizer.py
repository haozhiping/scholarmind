"""Query optimizer — rewrite + translate + HyDE (concurrent execution)."""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

from common.clients.llm import chat_complete
from common.config import settings
from common.logging import logger


@dataclass
class QueryBundle:
    original: str
    rewritten: str = ""
    translated_en: str = ""
    hyde_doc: str = ""

    @property
    def queries(self) -> list[str]:
        """Unique non-empty query variants for retrieval."""
        seen: set[str] = set()
        result: list[str] = []
        for q in (self.rewritten, self.translated_en, self.hyde_doc, self.original):
            q = q.strip()
            if q and q not in seen:
                seen.add(q)
                result.append(q)
        return result


_QUERY_REWRITE_SYSTEM = (
    "你是一个学术搜索助手。请将用户的简短/模糊/包含代指词的问题改写为清晰、"
    "自包含的检索用查询语句。只输出改写后的查询，不要解释。"
)

_QUERY_TRANSLATE_SYSTEM = (
    "You are a translator. Translate the following Chinese academic query into "
    "accurate English suitable for academic search. Output only the translation."
)

_HYDE_SYSTEM = (
    "You are a research assistant. Write a short hypothetical answer paragraph "
    "(in English, ~100 words) to the following academic question. "
    "The paragraph should sound like an excerpt from a real paper. "
    "Output only the paragraph."
)


async def _load_prompt(filename: str) -> str:
    """Load a prompt template from prompts/ directory."""
    from pathlib import Path
    p = Path(__file__).parents[3] / "prompts" / filename
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


async def _rewrite(question: str, history: Optional[list[dict]] = None) -> str:
    """Rewrite query to resolve co-references and ambiguity."""
    sys_prompt = await _load_prompt("query_rewrite.md") or _QUERY_REWRITE_SYSTEM
    history_text = ""
    if history:
        history_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in history[-6:]
        )
        sys_prompt += f"\n\nConversation history:\n{history_text}"
    return await chat_complete(
        prompt=question,
        system=sys_prompt,
        temperature=0.1,
        max_tokens=256,
    )


async def _translate(question: str) -> str:
    """Translate Chinese query to English (cross-lingual retrieval)."""
    sys_prompt = await _load_prompt("query_translate.md") or _QUERY_TRANSLATE_SYSTEM
    return await chat_complete(
        prompt=question,
        system=sys_prompt,
        temperature=0.0,
        max_tokens=256,
    )


async def _hyde(question: str) -> str:
    """Generate a hypothetical English document for retrieval."""
    sys_prompt = await _load_prompt("hyde.md") or _HYDE_SYSTEM
    return await chat_complete(
        prompt=question,
        system=sys_prompt,
        temperature=0.3,
        max_tokens=512,
    )


async def optimize_query(
    question: str,
    history: Optional[list[dict]] = None,
) -> QueryBundle:
    """Optimize a user question into multiple retrieval variants (concurrent).
    
    Respects feature flags:
      - ENABLE_QUERY_REWRITE
      - ENABLE_QUERY_TRANSLATION
      - ENABLE_HYDE
    
    When a feature is disabled, the corresponding field is left empty
    and the original question is used as fallback during search.
    """
    bundle = QueryBundle(original=question)

    tasks = []

    if settings.ENABLE_QUERY_REWRITE:
        tasks.append(("rewrite", _rewrite(question, history)))
    if settings.ENABLE_QUERY_TRANSLATION:
        tasks.append(("translate", _translate(question)))
    if settings.ENABLE_HYDE:
        tasks.append(("hyde", _hyde(question)))

    if not tasks:
        logger.info("Query optimization: all features disabled, using original")
        bundle.rewritten = question
        return bundle

    results = await asyncio.gather(*[t[1] for t in tasks], return_exceptions=True)

    for (name, _), result in zip(tasks, results):
        if isinstance(result, Exception):
            logger.warning(f"Query optimization '{name}' failed: {result}")
        elif name == "rewrite":
            bundle.rewritten = result.strip()
        elif name == "translate":
            bundle.translated_en = result.strip()
        elif name == "hyde":
            bundle.hyde_doc = result.strip()

    # Fallback: if rewrite failed, use original
    if not bundle.rewritten:
        bundle.rewritten = question

    logger.info(
        f"Query optimized: rewrote='{bundle.rewritten[:60]}...' "
        f"| translated='{bundle.translated_en[:60]}...' "
        f"| hyde_len={len(bundle.hyde_doc)}"
    )
    return bundle
