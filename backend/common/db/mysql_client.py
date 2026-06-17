import aiomysql
from typing import Optional, List, Dict, Any
from ..config import settings


class AsyncMySQLClient:
    """Async MySQL client (business DB `scholarmind`).

    Mirrors AsyncPGClient: a lazily-initialised connection pool with thin
    helpers. Uses %s placeholders (aiomysql/PyMySQL paramstyle) and
    autocommit so callers don't manage transactions for simple CRUD.
    """

    def __init__(self):
        self._pool: Optional[aiomysql.Pool] = None

    async def connect(self):
        if self._pool is None:
            self._pool = await aiomysql.create_pool(
                host=settings.MYSQL_HOST,
                port=settings.MYSQL_PORT,
                user=settings.MYSQL_USER,
                password=settings.MYSQL_PASSWORD,
                db=settings.MYSQL_DB,
                charset="utf8mb4",
                autocommit=True,
                minsize=1,
                maxsize=10,
            )

    async def disconnect(self):
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def fetchall(self, query: str, *args) -> List[Dict[str, Any]]:
        await self.connect()
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, args)
                return await cur.fetchall()

    async def fetchone(self, query: str, *args) -> Optional[Dict[str, Any]]:
        await self.connect()
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, args)
                return await cur.fetchone()

    async def fetchval(self, query: str, *args) -> Any:
        await self.connect()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args)
                row = await cur.fetchone()
                return row[0] if row else None

    async def execute(self, query: str, *args) -> int:
        """Run INSERT/UPDATE/DELETE. Returns lastrowid (INSERT) — for
        UPDATE/DELETE use execute_rowcount to get affected rows."""
        await self.connect()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args)
                return cur.lastrowid

    async def execute_rowcount(self, query: str, *args) -> int:
        """Run UPDATE/DELETE and return number of affected rows."""
        await self.connect()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, args)
                return cur.rowcount


# Shared singleton — import `mysql` and call helpers directly.
mysql = AsyncMySQLClient()
