import json
import time
import uuid
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime
from fastapi import HTTPException
from loguru import logger

from .schemas import Message, Conversation, ChatRequest, CiteInfo, SSEEvent
from .intent_router import IntentRouter
from .agent import ReviewAgent
from ..retrieval.retriever import HybridRetriever
from common.db.pg_client import AsyncPGClient
from common.db.redis_client import AsyncRedisClient
from common.db.mysql_client import mysql
from common.clients.llm import AsyncLLMClient
from common.exceptions import (
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

    async def query(
        self, request: ChatRequest, user_id: str = ""
    ) -> AsyncGenerator[str, None]:
        conv_id = request.conv_id or str(uuid.uuid4())
        t0 = time.perf_counter()
        full_response = ""
        citations: List[Dict[str, Any]] = []
        chunk_ids: List[str] = []
        intent_type = ""
        logger.info(f"Processing query: conv_id={conv_id}, query={request.query[:50]}...")

        # tiny helper: accumulate token/cite from a JSON-line chunk
        def _accum(line: str):
            nonlocal full_response
            try:
                obj = json.loads(line)
                etype = obj.get("event", "")
                if etype == "token":
                    full_response += obj.get("data", {}).get("content", "")
                elif etype == "cite":
                    citations.append(obj.get("data", {}))
                    cid = obj.get("data", {}).get("chunk_id")
                    if cid:
                        chunk_ids.append(cid)
            except (json.JSONDecodeError, TypeError):
                pass

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
                    _accum(chunk)
                    yield chunk
            elif intent_type == "knowledge":
                async for chunk in self._handle_knowledge(request.query, history, conv_id):
                    _accum(chunk)
                    yield chunk
            elif intent_type == "complex":
                async for chunk in self._handle_complex(request.query, history, conv_id):
                    _accum(chunk)
                    yield chunk
            elif intent_type == "followup":
                async for chunk in self._handle_followup(request.query, history, conv_id):
                    _accum(chunk)
                    yield chunk
            else:
                logger.warning(f"Unknown intent type: {intent_type}")
                yield json.dumps({"event": "error", "data": {"message": "Unknown intent type"}})

            # ---- persistent write AFTER streaming is complete ----
            latency_ms = int((time.perf_counter() - t0) * 1000)

            meta: Dict[str, Any] = {"intent_type": intent_type}
            if citations:
                meta["citations"] = citations

            await self.add_message(
                conv_id, "assistant", full_response, meta,
            )

            await self._log_query(
                user_id=user_id,
                question=request.query,
                intent_type=intent_type,
                chunk_ids=chunk_ids,
                latency_ms=latency_ms,
            )

            logger.info(
                f"Query completed: conv_id={conv_id}, intent={intent_type}, "
                f"len={len(full_response)}, latency={latency_ms}ms"
            )

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

    async def get_messages(self, conv_id: str) -> List[Dict[str, Any]]:
        """Return all messages for a conversation (for the REST endpoint)."""
        try:
            rows = await self.pg_client.fetch(
                """
                SELECT msg_id, conv_id, role, content, metadata, created_at
                FROM messages
                WHERE conv_id = $1
                ORDER BY created_at ASC
                """,
                conv_id
            )

            messages = []
            for row in rows:
                citations = []
                meta = row.get("metadata")
                if isinstance(meta, str):
                    import json as _json
                    meta = _json.loads(meta)
                if isinstance(meta, dict):
                    # extract citations if stored in metadata
                    raw_cites = meta.get("citations")
                    if isinstance(raw_cites, list):
                        citations = raw_cites

                messages.append({
                    "id": str(row["msg_id"]),
                    "conversation_id": str(row["conv_id"]),
                    "role": row["role"],
                    "content": row["content"],
                    "citations": citations,
                    "created_at": row["created_at"],
                })

            logger.debug(f"Retrieved {len(messages)} messages for conv_id={conv_id}")
            return messages
        except Exception as e:
            logger.error(f"Failed to get messages: conv_id={conv_id}, error={e}")
            raise DatabaseException(f"Failed to get messages: {e}")

    async def save_feedback(
        self, message_id: str, is_positive: bool, reason: str = "", user_id: str = ""
    ):
        """Attach user feedback to the latest real query log (MySQL).

        Updates the ``feedback`` TINYINT column (1 = up / -1 = down) on the
        user's most recent query_logs row. We deliberately do NOT INSERT a new
        row: the previous implementation created synthetic
        ``question="Feedback for msg …"`` rows that polluted the query-log view
        with fake question entries.
        """
        try:
            feedback_val = 1 if is_positive else -1
            user_id_int = int(user_id) if user_id else 0

            affected = await mysql.execute_rowcount(
                """
                UPDATE query_logs SET feedback=%s
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                feedback_val, user_id_int,
            )
            if affected == 0:
                logger.warning(
                    f"No query_log to attach feedback: user_id={user_id_int}, "
                    f"msg_id={message_id}"
                )
            else:
                logger.info(
                    f"Feedback saved: msg_id={message_id}, positive={is_positive}"
                )
        except Exception as e:
            logger.error(f"Failed to save feedback: msg_id={message_id}, error={e}")
            raise DatabaseException(f"Failed to save feedback: {e}")

    async def _log_query(
        self,
        user_id: str,
        question: str,
        intent_type: str = "",
        chunk_ids: Optional[List[str]] = None,
        latency_ms: int = 0,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ):
        """Write a query-log row to MySQL after every query.

        Columns filled: user_id, question, rewritten_query (intent hint),
        retrieved_chunk_ids, top_k, latency_ms, prompt_tokens, completion_tokens.
        """
        try:
            user_id_int = int(user_id) if user_id else 0
            rewritten = f"[intent={intent_type}]" if intent_type else None
            chunk_ids_json = json.dumps(chunk_ids) if chunk_ids else None
            top_k = len(chunk_ids) if chunk_ids else None

            await mysql.execute(
                """
                INSERT INTO query_logs
                  (user_id, question, rewritten_query, retrieved_chunk_ids,
                   top_k, latency_ms, prompt_tokens, completion_tokens)
                VALUES
                  (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                user_id_int,
                question,
                rewritten,
                chunk_ids_json,
                top_k,
                latency_ms,
                prompt_tokens,
                completion_tokens,
            )
            logger.debug(
                f"Query log written: user_id={user_id_int}, latency={latency_ms}ms, "
                f"chunks={top_k}"
            )
        except Exception as e:
            logger.warning(f"Failed to write query_log (non-fatal): {e}")

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