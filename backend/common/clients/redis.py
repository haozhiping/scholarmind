"""
Redis connection + RQ queue + small cache helpers.

- get_redis(): shared sync Redis connection (used by RQ and cache helpers).
- get_ingest_queue(): RQ Queue bound to the "ingest" queue.
- cache_get_json / cache_set_json: tiny JSON cache helpers with TTL.
"""
import json
from typing import Any, Optional

from redis import Redis
from rq import Queue

from common.config import settings
from common.logging import logger

INGEST_QUEUE_NAME = "ingest"

_redis: Optional[Redis] = None


def get_redis() -> Redis:
    """Return a shared Redis connection (lazily created)."""
    global _redis
    if _redis is None:
        _redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
        )
    return _redis


def get_ingest_queue() -> Queue:
    """Return the RQ queue used for parse/index jobs."""
    return Queue(INGEST_QUEUE_NAME, connection=get_redis())


# ---------------------------------------------------------------------------
# Cache helpers (best-effort; failures never raise)
# ---------------------------------------------------------------------------

def cache_get_json(key: str) -> Optional[Any]:
    try:
        raw = get_redis().get(key)
        return json.loads(raw) if raw else None
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[redis] cache_get failed: {e}")
        return None


def cache_set_json(key: str, value: Any, ttl: int = 3600) -> None:
    try:
        get_redis().set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[redis] cache_set failed: {e}")
