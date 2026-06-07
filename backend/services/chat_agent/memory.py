"""
Conversation memory backed by PostgreSQL (chat_agent only).

  - get_or_create_conversation(user_id, title, ...) -> conversation_id
  - get_history(conversation_id, limit) -> [{role, content}]
  - save_message(conversation_id, role, content, citations)
  - list_conversations / get_conversation
"""
from __future__ import annotations

import json
from typing import Optional

from sqlalchemy import text

from common.db.pg import get_pg_session
from common.logging import logger


async def get_or_create_conversation(
    user_id: int,
    title: Optional[str] = None,
    conversation_id: Optional[int] = None,
) -> int:
    async with get_pg_session() as db:
        if conversation_id:
            res = await db.execute(
                text("SELECT id FROM conversations WHERE id = :id AND user_id = :u"),
                {"id": conversation_id, "u": user_id},
            )
            if res.first():
                return conversation_id
        res = await db.execute(
            text("""
                INSERT INTO conversations (user_id, title)
                VALUES (:u, :t) RETURNING id
            """),
            {"u": user_id, "t": title or "新会话"},
        )
        new_id = res.scalar()
        return new_id


async def get_history(conversation_id: int, limit: int = 10) -> list[dict]:
    if not conversation_id:
        return []
    try:
        async with get_pg_session() as db:
            res = await db.execute(
                text("""
                    SELECT role, content FROM messages
                    WHERE conversation_id = :c
                    ORDER BY id DESC LIMIT :n
                """),
                {"c": conversation_id, "n": limit},
            )
            rows = [{"role": r["role"], "content": r["content"]} for r in res.mappings().all()]
            return list(reversed(rows))
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[memory] get_history failed: {e}")
        return []


async def save_message(
    conversation_id: int,
    role: str,
    content: str,
    citations: Optional[list] = None,
) -> None:
    try:
        async with get_pg_session() as db:
            await db.execute(
                text("""
                    INSERT INTO messages (conversation_id, role, content, citations)
                    VALUES (:c, :r, :ct, CAST(:cite AS JSONB))
                """),
                {
                    "c": conversation_id,
                    "r": role,
                    "ct": content,
                    "cite": json.dumps(citations or [], ensure_ascii=False),
                },
            )
            await db.execute(
                text("UPDATE conversations SET updated_at = now() WHERE id = :c"),
                {"c": conversation_id},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[memory] save_message failed: {e}")


async def list_conversations(user_id: int) -> list[dict]:
    async with get_pg_session() as db:
        res = await db.execute(
            text("""
                SELECT id, title, created_at, updated_at FROM conversations
                WHERE user_id = :u ORDER BY updated_at DESC
            """),
            {"u": user_id},
        )
        return [dict(r) for r in res.mappings().all()]


async def get_messages(conversation_id: int, user_id: int) -> list[dict]:
    async with get_pg_session() as db:
        owns = await db.execute(
            text("SELECT id FROM conversations WHERE id = :c AND user_id = :u"),
            {"c": conversation_id, "u": user_id},
        )
        if not owns.first():
            return []
        res = await db.execute(
            text("""
                SELECT id, conversation_id, role, content, citations, created_at
                FROM messages WHERE conversation_id = :c ORDER BY id ASC
            """),
            {"c": conversation_id},
        )
        out = []
        for r in res.mappings().all():
            row = dict(r)
            cites = row.get("citations")
            if isinstance(cites, str):
                try:
                    row["citations"] = json.loads(cites)
                except json.JSONDecodeError:
                    row["citations"] = []
            out.append(row)
        return out
