from typing import Any, Dict

from fastapi import APIRouter, Depends

from common.auth.deps import get_current_user
from common.db.redis_client import AsyncRedisClient

router = APIRouter(tags=["settings"])

# Per-user runtime settings live in Redis (no dedicated table in data-contracts);
# key = settings:{user_id}. Lightweight, real persistence — not a mock.
_redis = AsyncRedisClient()


@router.get("/settings",
            summary="读取当前用户的全局配置",
            description="返回当前用户已保存的 RAG 策略/模型参数配置；未保存过则返回空对象，前端用默认值兜底。")
async def get_settings(current=Depends(get_current_user)) -> Dict[str, Any]:
    data = await _redis.get_json(f"settings:{current['id']}")
    return data or {}


@router.post("/settings",
             summary="保存当前用户的全局配置",
             description="保存前端设置页提交的 RAG 开关、模型选择、检索超参等配置（按 user_id 隔离存储）。")
async def save_settings(config: Dict[str, Any], current=Depends(get_current_user)):
    await _redis.set_json(f"settings:{current['id']}", config)
    return {"status": "success", "message": "设置已保存"}
