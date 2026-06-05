import json
from typing import List, Dict, Any, Optional
from .prompts import INTENT_ROUTER_PROMPT
from ...common.clients.llm import AsyncLLMClient

class IntentRouter:
    def __init__(self, llm_client: Optional[AsyncLLMClient] = None):
        self.llm_client = llm_client or AsyncLLMClient()

    async def route_intent(
        self, query: str, history: Optional[List[Dict[str, str]]] = None
    ) -> Dict[str, Any]:
        history_str = json.dumps(history, ensure_ascii=False) if history else "无"
        
        prompt = INTENT_ROUTER_PROMPT.format(
            query=query,
            history=history_str
        )
        
        response = await self.llm_client.chat_completion(
            messages=[{"role": "user", "content": prompt}],
            model="qwen3-7b",
            temperature=0.0
        )
        
        try:
            result = json.loads(response)
            return {
                "intent_type": result.get("intent_type", "knowledge"),
                "confidence": result.get("confidence", 0.5),
                "reasoning": result.get("reasoning", "")
            }
        except (json.JSONDecodeError, KeyError):
            return {
                "intent_type": "knowledge",
                "confidence": 0.5,
                "reasoning": "解析失败，默认归类为知识问答"
            }

    async def should_retrieve(self, query: str, history: Optional[List[Dict[str, str]]] = None) -> bool:
        result = await self.route_intent(query, history)
        return result["intent_type"] in ["knowledge", "complex", "followup"]