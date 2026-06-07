"""
Auth router — real implementation backed by MySQL `users` + JWT.
Passwords stored as bcrypt hashes; login returns a JWT carrying user_id (tenant key).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.auth import Token, UserLogin, UserMe, UserRegister
from common.auth import create_access_token, get_current_user_id, hash_password, verify_password
from common.db.mysql import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserMe, status_code=status.HTTP_201_CREATED,
             summary="用户注册",
             description="注册新账号，用户名/邮箱唯一，密码 bcrypt 加密存储。")
async def register(user_data: UserRegister, db: AsyncSession = Depends(get_db)):
    # Uniqueness check
    existing = await db.execute(
        text("SELECT id FROM users WHERE username = :u OR email = :e"),
        {"u": user_data.username, "e": user_data.email},
    )
    if existing.first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名或邮箱已存在")

    result = await db.execute(
        text("""
            INSERT INTO users (username, email, password_hash, role)
            VALUES (:username, :email, :password_hash, 'user')
        """),
        {
            "username": user_data.username,
            "email": user_data.email,
            "password_hash": hash_password(user_data.password),
        },
    )
    user_id = result.lastrowid
    return UserMe(id=user_id, username=user_data.username, email=user_data.email, role="user", is_active=True)


@router.post("/login", response_model=Token,
             summary="用户登录",
             description="用户名+密码登录，返回 JWT access_token（载荷含 user_id）。")
async def login(credentials: UserLogin, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT id, password_hash, role FROM users WHERE username = :u"),
        {"u": credentials.username},
    )
    row = result.mappings().first()
    if not row or not verify_password(credentials.password, row["password_hash"]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")

    token = create_access_token(user_id=row["id"], role=row["role"])
    return Token(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserMe,
            summary="当前登录用户信息",
            description="返回当前 JWT 对应的用户信息。")
async def get_me(user_id: int = Depends(get_current_user_id), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        text("SELECT id, username, email, role FROM users WHERE id = :id"),
        {"id": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return UserMe(id=row["id"], username=row["username"], email=row["email"], role=row["role"], is_active=True)
