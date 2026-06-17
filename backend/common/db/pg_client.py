import asyncpg
from typing import Optional, List, Dict, Any
from ..config import settings

class AsyncPGClient:
    def __init__(self, db_name: Optional[str] = None):
        self.db_name = db_name or settings.PG_DB
        self._connection_pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        if self._connection_pool is None:
            self._connection_pool = await asyncpg.create_pool(
                host=settings.PG_HOST,
                port=settings.PG_PORT,
                user=settings.PG_USER,
                password=settings.PG_PASSWORD,
                database=self.db_name,
                min_size=1,
                max_size=10
            )

    async def disconnect(self):
        if self._connection_pool is not None:
            await self._connection_pool.close()
            self._connection_pool = None

    async def execute(self, query: str, *args) -> None:
        await self.connect()
        async with self._connection_pool.acquire() as conn:
            await conn.execute(query, *args)

    async def fetch(self, query: str, *args) -> List[Dict[str, Any]]:
        await self.connect()
        async with self._connection_pool.acquire() as conn:
            records = await conn.fetch(query, *args)
            return [dict(record) for record in records]

    async def fetchrow(self, query: str, *args) -> Optional[Dict[str, Any]]:
        await self.connect()
        async with self._connection_pool.acquire() as conn:
            record = await conn.fetchrow(query, *args)
            return dict(record) if record else None

    async def fetchval(self, query: str, *args) -> Any:
        await self.connect()
        async with self._connection_pool.acquire() as conn:
            return await conn.fetchval(query, *args)