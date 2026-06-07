"""
Auth primitives: bcrypt password hashing, JWT issue/verify, and a FastAPI dependency
that extracts the current user_id from the Bearer token (multi-tenant isolation source).

Security red lines (see auth/CLAUDE.md): SECRET_KEY only from .env, passwords only bcrypt,
every protected route depends on get_current_user_id -> request carries a trusted user_id.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from common.config import settings

_bearer = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Password hashing (bcrypt)
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------

def create_access_token(user_id: int, role: str = "user") -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"user_id": user_id, "role": role, "exp": expire, "sub": str(user_id)}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------

async def get_current_user_id(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> int:
    """Decode the Bearer token and return user_id. Raises 401 if missing/invalid."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    payload = decode_token(creds.credentials)
    if not payload or "user_id" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    return int(payload["user_id"])


async def get_current_user(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """Return full token payload ({user_id, role, ...})."""
    if creds is None or not creds.credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    payload = decode_token(creds.credentials)
    if not payload or "user_id" not in payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效或已过期")
    return payload
