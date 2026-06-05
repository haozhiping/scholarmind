"""FastAPI auth dependency: extract + verify the Bearer JWT, return the
current user as a dict. Every business router depends on this so that
`user_id` is always available for multi-tenant filtering.
"""
from typing import Any, Dict, Optional

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError

from ..exceptions import AuthException
from .security import decode_token

# auto_error=False so a missing header yields a clean AuthException (401)
# routed through our exception_handler, not FastAPI's default 403.
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Dict[str, Any]:
    if credentials is None or not credentials.credentials:
        raise AuthException("缺少认证令牌")
    try:
        payload = decode_token(credentials.credentials)
    except JWTError:
        raise AuthException("令牌无效或已过期")

    sub = payload.get("sub")
    if sub is None:
        raise AuthException("令牌缺少用户标识")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        raise AuthException("令牌用户标识非法")

    return {
        "id": user_id,
        "username": payload.get("username"),
        "role": payload.get("role", "user"),
    }
