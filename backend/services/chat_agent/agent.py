import json
from typing import List, Dict, Any, Optional, AsyncGenerator
from llama_index.core.agent import ReActAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI
from .prompts import REVIEW_GENERATION_PROMPT
from services.retrieval.searcher import hybrid_search, SearchScope, ScoredChunk
from services.retrieval.query_optimizer import optimize_query
from common.clients.llm import chat_complete
from common.config import settings
from common.logging import logger


class ReviewAgent:
    def __init__(self):
        self.llm = OpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.LLM_API_KEY,
            api_base=settings.LLM_BASE_URL,
            temperature=settings.LLM_TEMPERATURE,
        )

    async def _search_papers(self, user_id: int, query: str, top_k: int = 5) -> List[Dict[str, Any]]:
        """Search tool for the agent — returns scored chunks."""
        from services.retrieval.query_optimizer import QueryBundle
        bundle = QueryBundle(original=query, rewritten=query)
        scope = SearchScope(user_id=user_id)
        chunks = await hybrid_search(bundle, scope, top_k=top_k)
        return [
            {
                "chunk_id": c.chunk_id,
                "content": c.content_en,
                "content_zh": c.content_zh,
                "paper_id": c.paper_id,
                "page": c.page_num,
                "image_key": c.image_key,
                "score": c.score,
            }
            for c in chunks
        ]

    async def generate_review(
        self, user_id: int, query: str, scope: Optional[SearchScope] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Generate a literature review using LlamaIndex ReAct agent."""
        scope = scope or SearchScope(user_id=user_id)

        # Step 1: Decompose query into sub-questions, search each
        search_tool = FunctionTool.from_defaults(
            fn=lambda q, k=10: self._search_papers(user_id, q, k),
            name="search_papers",
            description="Search academic papers and return relevant excerpts.",
        )

        agent = ReActAgent.from_tools(
            [search_tool],
            llm=self.llm,
            verbose=True,
        )

        response = await agent.achat(query)

        # Step 2: Collect retrieved papers from agent history
        papers = []
        for step in agent.chat_history:
            if hasattr(step, "tool_calls"):
                for call in step.tool_calls:
                    if getattr(call, "tool_name", "") == "search_papers":
                        results = call.results if hasattr(call, "results") else []
                        papers.extend(results)

        # Deduplicate
        unique_papers: Dict[str, dict] = {}
        for p in papers:
            paper_id = p.get("paper_id", str(id(p)))
            if paper_id not in unique_papers:
                unique_papers[paper_id] = p

        papers_list = list(unique_papers.values())
        papers_context = "\n\n".join(
            f"论文[{i+1}]: {p.get('content', '')}"
            for i, p in enumerate(papers_list)
        )

        # Step 3: Synthesize review with LLM
        prompt = REVIEW_GENERATION_PROMPT.format(
            papers=papers_context,
            query=query,
        )

        # Stream token-by-token
        from openai import AsyncOpenAI as _AsyncOpenAI
        client = _AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        stream = await client.chat.completions.create(
            model=settings.LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield {"event": "token", "data": {"content": delta}}

                # Emit cite events when [N] appears in token
                for i, p in enumerate(papers_list):
                    if f"[{i+1}]" in delta:
                        yield {
                            "event": "cite",
                            "data": {
                                "paper_id": p.get("paper_id"),
                                "page": p.get("page"),
                                "chunk_id": p.get("chunk_id"),
                                "image_key": p.get("image_key"),
                            },
                        }

        yield {"event": "done", "data": {"finish_reason": "stop", "papers_count": len(papers_list)}}

    async def generate_comparison(
        self, user_id: int, paper_ids: List[int], query: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Compare multiple papers on a given topic."""
        papers_content = []

        for paper_id in paper_ids:
            scope = SearchScope(user_id=user_id, paper_ids=[paper_id])
            from services.retrieval.query_optimizer import QueryBundle
            bundle = QueryBundle(original=query, rewritten=query)
            chunks = await hybrid_search(bundle, scope, top_k=10)
            if chunks:
                content = "\n".join(c.content_en for c in chunks)
                papers_content.append({
                    "paper_id": paper_id,
                    "content": content,
                })

        if not papers_content:
            yield {"event": "done", "data": {"finish_reason": "error", "msg": "未找到相关论文内容"}}
            return

        papers_context = "\n\n".join(
            f"论文[{i+1}](ID:{p['paper_id']}): {p['content']}"
            for i, p in enumerate(papers_content)
        )

        comparison_prompt = f"""
请对以下论文进行对比分析：

{papers_context}

用户请求：{query}

请输出结构化的对比分析报告，使用角标[n]引用对应论文。
"""

        from openai import AsyncOpenAI as _AsyncOpenAI
        client = _AsyncOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
        )
        stream = await client.chat.completions.create(
            model=settings.LLM_REASON_MODEL,
            messages=[{"role": "user", "content": comparison_prompt}],
            temperature=settings.LLM_TEMPERATURE,
            max_tokens=settings.LLM_MAX_TOKENS,
            stream=True,
        )

        async for chunk in stream:
            delta = chunk.choices[0].delta.content or ""
            if delta:
                yield {"event": "token", "data": {"content": delta}}

                for i, p in enumerate(papers_content):
                    if f"[{i+1}]" in delta:
                        yield {
                            "event": "cite",
                            "data": {"paper_id": p["paper_id"]},
                        }

        yield {"event": "done", "data": {"finish_reason": "stop"}}