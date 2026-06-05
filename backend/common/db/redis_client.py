import json
from typing import Optional, Any
import redis.asyncio as redis
from ..config import settings

class AsyncRedisClient:
    def __init__(self):
        self._client: Optional[redis.Redis] = None

    async def connect(self):
        if self._client is None:
            self._client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=settings.REDIS_DB,
                decode_responses=True
            )

    async def disconnect(self):
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def get(self, key: str) -> Optional[str]:
        await self.connect()
        return await self._client.get(key)

    async def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        await self.connect()
        return await self._client.set(key, value, ex=ex)

    async def delete(self, key: str) -> int:
        await self.connect()
        return await self._client.delete(key)

    async def exists(self, key: str) -> bool:
        await self.connect()
        return await self._client.exists(key) > 0

    async def get_json(self, key: str) -> Optional[Any]:
        await self.connect()
        value = await self._client.get(key)
        if value is None:
            return None
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None

    async def set_json(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        await self.connect()
        return await self._client.set(key, json.dumps(value), ex=ex)

    async def incr(self, key: str) -> int:
        await self.connect()
        return await self._client.incr(key)

    async def expire(self, key: str, seconds: int) -> bool:
        await self.connect()
        return await self._client.expire(key, seconds)

    async def keys(self, pattern: str) -> list:
        await self.connect()
        return await self._client.keys(pattern)

    async def hget(self, key: str, field: str) -> Optional[str]:
        await self.connect()
        return await self._client.hget(key, field)

    async def hset(self, key: str, field: str, value: str) -> int:
        await self.connect()
        return await self._client.hset(key, field, value)

    async def hgetall(self, key: str) -> dict:
        await self.connect()
        return await self._client.hgetall(key)