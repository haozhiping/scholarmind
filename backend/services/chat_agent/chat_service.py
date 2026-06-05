import json
import uuid
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime
from fastapi import HTTPException
from fastapi.responses import EventSourceResponse
from loguru import logger

from .schemas import Message, Conversation, ChatRequest, CiteInfo, SSEEvent
from .intent_router import IntentRouter
from .agent import ReviewAgent
from ..retrieval.retriever import HybridRetriever
from ...common.db.pg_client import AsyncPGClient
from ...common.db.redis_client import AsyncRedisClient
from ...common.clients.llm import AsyncLLMClient
from ...common.exceptions import (
    DatabaseException, 
    LLMException, 
    RedisException, 
    NotFoundException
)
from .prompts import CHAT_WITH_CITATION_PROMPT, CHITCHAT_PROMPT

class ChatService:
    def __init__(self):
        self.pg_client = AsyncPGClient(db_name="scholarmind_memory")
        self.redis_client = AsyncRedisClient()
        self.llm_client = AsyncLLMClient()
        self.intent_router = IntentRouter(self.llm_client)
        self.retriever = HybridRetriever()
        self.review_agent = ReviewAgent(self.llm_client, self.retriever)

    async def create_conversation(self, user_id: Optional[str] = None) -> Conversation:
        try:
            conv_id = uuid.uuid4()
            conversation = Conversation(
                conv_id=conv_id,
                user_id=user_id,
                title=None,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            
            await self.pg_client.execute(
                """
                INSERT INTO conversations (conv_id, user_id, title, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5)
                """,
                str(conversation.conv_id),
                conversation.user_id,
                conversation.title,
                conversation.created_at,
                conversation.updated_at
            )
            
            logger.info(f"Created conversation: conv_id={conversation.conv_id}, user_id={user_id}")
            return conversation
        except Exception as e:
            logger.error(f"Failed to create conversation: {e}")
            raise DatabaseException(f"Failed to create conversation: {e}")

    async def add_message(self, conv_id: str, role: str, content: str, metadata: Optional[Dict] = None):
        try:
            msg_id = str(uuid.uuid4())
            await self.pg_client.execute(
                """
                INSERT INTO messages (msg_id, conv_id, role, content, created_at, metadata)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                msg_id,
                conv_id,
                role,
                content,
                datetime.utcnow(),
                json.dumps(metadata) if metadata else None
            )
            
            await self.pg_client.execute(
                """
                UPDATE conversations SET updated_at = $1 WHERE conv_id = $2
                """,
                datetime.utcnow(),
                conv_id
            )
            
            await self._update_redis_cache(conv_id, role, content)
            logger.debug(f"Added message: msg_id={msg_id}, conv_id={conv_id}, role={role}")
        except Exception as e:
            logger.error(f"Failed to add message: conv_id={conv_id}, error={e}")
            raise DatabaseException(f"Failed to add message: {e}")

    async def get_history(self, conv_id: str, window_size: int = 20) -> List[Dict[str, str]]:
        cache_key = f"sess:{conv_id}"
        
        try:
            cached = await self.redis_client.get(cache_key)
            if cached:
                logger.debug(f"Cache hit for conversation: {conv_id}")
                history = json.loads(cached)
                return history[-window_size:]
        except Exception as e:
            logger.warning(f"Failed to get from cache: {e}, falling back to DB")
        
        try:
            rows = await self.pg_client.fetch(
                """
                SELECT role, content FROM messages 
                WHERE conv_id = $1 
                ORDER BY created_at DESC 
                LIMIT $2
                """,
                conv_id,
                window_size
            )
            
            history = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]
            
            try:
                await self.redis_client.set(cache_key, json.dumps(history), ex=3600)
            except Exception as e:
                logger.warning(f"Failed to set cache: {e}")
            
            return history
        except Exception as e:
            logger.error(f"Failed to get history: conv_id={conv_id}, error={e}")
            raise DatabaseException(f"Failed to get conversation history: {e}")

    async def _update_redis_cache(self, conv_id: str, role: str, content: str):
        try:
            cache_key = f"sess:{conv_id}"
            cached = await self.redis_client.get(cache_key)
            
            if cached:
                history = json.loads(cached)
            else:
                history = []
            
            history.append({"role": role, "content": content})
            
            await self.redis_client.set(cache_key, json.dumps(history), ex=3600)
        except Exception as e:
            logger.warning(f"Failed to update cache: {e}")

    async def query(self, request: ChatRequest) -> AsyncGenerator[str, None]:
        conv_id = request.conv_id or str(uuid.uuid4())
        logger.info(f"Processing query: conv_id={conv_id}, query={request.query[:50]}...")
        
        try:
            if not request.conv_id:
                await self.create_conversation()
            
            await self.add_message(conv_id, "user", request.query)
            
            history = await self.get_history(conv_id)
            
            intent_result = await self.intent_router.route_intent(request.query, history)
            intent_type = intent_result["intent_type"]
            logger.info(f"Intent classified: {intent_type}")
            
            if intent_type == "chitchat":
                async for chunk in self._handle_chitchat(request.query):
                    yield chunk
            elif intent_type == "knowledge":
                async for chunk in self._handle_knowledge(request.query, history, conv_id):
                    yield chunk
            elif intent_type == "complex":
                async for chunk in self._handle_complex(request.query, history, conv_id):
                    yield chunk
            elif intent_type == "followup":
                async for chunk in self._handle_followup(request.query, history, conv_id):
                    yield chunk
            else:
                logger.warning(f"Unknown intent type: {intent_type}")
                yield json.dumps({"event": "error", "data": {"message": "Unknown intent type"}})
            
            await self.add_message(conv_id, "assistant", "", {"intent_type": intent_type})
            logger.info(f"Query completed: conv_id={conv_id}, intent={intent_type}")
            
        except Exception as e:
            logger.error(f"Error processing query: conv_id={conv_id}, error={e}")
            yield json.dumps({
                "event": "error", 
                "data": {"message": str(e), "conv_id": conv_id}
            })

    async def _handle_chitchat(self, query: str) -> AsyncGenerator[str, None]:
        try:
            prompt = CHITCHAT_PROMPT.format(query=query)
            
            async for token in self.llm_client.stream_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model="qwen3-7b"
            ):
                yield json.dumps({
                    "event": "token",
                    "data": {"content": token}
                })
            
            yield json.dumps({"event": "done", "data": {"finish_reason": "stop"}})
        except Exception as e:
            logger.error(f"Chitchat handler error: {e}")
            raise LLMException(f"Failed to generate response: {e}")

    async def _handle_knowledge(self, query: str, history: List[Dict], conv_id: str) -> AsyncGenerator[str, None]:
        try:
            results = await self.retriever.retrieve(query, top_k=5)
            logger.debug(f"Retrieved {len(results)} results for query")
            
            context = "\n\n".join([f"[{i+1}] {r['content']}" for i, r in enumerate(results)])
            prompt = CHAT_WITH_CITATION_PROMPT.format(context=context, question=query)
            
            cited_papers = {}
            for i, r in enumerate(results):
                cited_papers[i+1] = {
                    "paper_id": r.get("paper_id"),
                    "chunk_id": r.get("chunk_id"),
                    "page": r.get("page")
                }
            
            async for token in self.llm_client.stream_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model="qwen3-7b"
            ):
                yield json.dumps({
                    "event": "token",
                    "data": {"content": token}
                })
                
                for cite_num, info in cited_papers.items():
                    if f"[{cite_num}]" in token:
                        yield json.dumps({
                            "event": "cite",
                            "data": info
                        })
            
            yield json.dumps({"event": "done", "data": {"finish_reason": "stop"}})
        except Exception as e:
            logger.error(f"Knowledge handler error: {e}")
            raise LLMException(f"Failed to process knowledge query: {e}")

    async def _handle_complex(self, query: str, history: List[Dict], conv_id: str) -> AsyncGenerator[str, None]:
        try:
            async for event in self.review_agent.generate_review(query):
                yield json.dumps(event)
        except Exception as e:
            logger.error(f"Complex query handler error: {e}")
            raise LLMException(f"Failed to generate review: {e}")

    async def _handle_followup(self, query: str, history: List[Dict], conv_id: str) -> AsyncGenerator[str, None]:
        try:
            full_context = "\n".join([f"{h['role']}: {h['content']}" for h in history[-10:]])
            full_query = f"历史对话：\n{full_context}\n\n当前问题：{query}"
            
            results = await self.retriever.retrieve(full_query, top_k=5)
            
            context = "\n\n".join([f"[{i+1}] {r['content']}" for i, r in enumerate(results)])
            prompt = CHAT_WITH_CITATION_PROMPT.format(context=context, question=query)
            
            async for token in self.llm_client.stream_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                model="qwen3-7b"
            ):
                yield json.dumps({
                    "event": "token",
                    "data": {"content": token}
                })
            
            yield json.dumps({"event": "done", "data": {"finish_reason": "stop"}})
        except Exception as e:
            logger.error(f"Followup handler error: {e}")
            raise LLMException(f"Failed to process followup query: {e}")

    async def get_conversations(self, user_id: Optional[str] = None) -> List[Conversation]:
        try:
            if user_id:
                rows = await self.pg_client.fetch(
                    "SELECT * FROM conversations WHERE user_id = $1 ORDER BY updated_at DESC",
                    user_id
                )
            else:
                rows = await self.pg_client.fetch(
                    "SELECT * FROM conversations ORDER BY updated_at DESC"
                )
            
            conversations = [Conversation(**row) for row in rows]
            logger.debug(f"Retrieved {len(conversations)} conversations")
            return conversations
        except Exception as e:
            logger.error(f"Failed to get conversations: user_id={user_id}, error={e}")
            raise DatabaseException(f"Failed to get conversations: {e}")

    async def get_conversation(self, conv_id: str) -> Optional[Conversation]:
        try:
            row = await self.pg_client.fetchrow(
                "SELECT * FROM conversations WHERE conv_id = $1",
                conv_id
            )
            
            if row:
                return Conversation(**row)
            else:
                logger.warning(f"Conversation not found: {conv_id}")
                raise NotFoundException(f"Conversation not found: {conv_id}")
        except NotFoundException:
            raise
        except Exception as e:
            logger.error(f"Failed to get conversation: conv_id={conv_id}, error={e}")
            raise DatabaseException(f"Failed to get conversation: {e}")

    async def delete_conversation(self, conv_id: str):
        try:
            await self.pg_client.execute(
                "DELETE FROM messages WHERE conv_id = $1",
                conv_id
            )
            await self.pg_client.execute(
                "DELETE FROM conversations WHERE conv_id = $1",
                conv_id
            )
            await self.redis_client.delete(f"sess:{conv_id}")
            
            logger.info(f"Deleted conversation: {conv_id}")
        except Exception as e:
            logger.error(f"Failed to delete conversation: conv_id={conv_id}, error={e}")
            raise DatabaseException(f"Failed to delete conversation: {e}")