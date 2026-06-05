from fastapi import APIRouter, Depends, status

from app.schemas.auth import UserRegister, UserLogin, Token, UserMe
from common.auth.deps import get_current_user
from common.auth.security import create_access_token, hash_password, verify_password
from common.db.mysql_client import mysql
from common.exceptions import AuthException, NotFoundException, ValidationException

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserMe, status_code=status.HTTP_201_CREATED,
             summary="用户注册",
             description="注册新账号，返回用户信息。用户名/邮箱唯一，密码 bcrypt 加密存储。")
async def register(user_data: UserRegister):
    # Uniqueness check on username/email before insert.
    existing = await mysql.fetchone(
        "SELECT id FROM users WHERE username=%s OR email=%s",
        user_data.username, user_data.email,
    )
    if existing:
        raise ValidationException("用户名或邮箱已被注册")

    pwd_hash = hash_password(user_data.password)
    user_id = await mysql.execute(
        "INSERT INTO users (username, email, password_hash, role) "
        "VALUES (%s, %s, %s, 'user')",
        user_data.username, user_data.email, pwd_hash,
    )
    return UserMe(
        id=user_id,
        username=user_data.username,
        email=user_data.email,
        role="user",
        is_active=True,
    )


@router.post("/login", response_model=Token,
             summary="用户登录",
             description="用户名+密码登录，返回 JWT access_token（有效期 7 天）。后续所有业务接口需在 Header 带 `Authorization: Bearer <token>`。")
async def login(credentials: UserLogin):
    user = await mysql.fetchone(
        "SELECT id, username, password_hash, role FROM users WHERE username=%s",
        credentials.username,
    )
    if not user or not verify_password(credentials.password, user["password_hash"]):
        raise AuthException("用户名或密码错误")

    token = create_access_token(
        user["id"], {"username": user["username"], "role": user["role"]}
    )
    return Token(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserMe,
            summary="当前登录用户信息",
            description="返回当前 JWT 对应的用户信息，可用于前端初始化用户状态。")
async def get_me(current=Depends(get_current_user)):
    user = await mysql.fetchone(
        "SELECT id, username, email, role FROM users WHERE id=%s",
        current["id"],
    )
    if not user:
        raise NotFoundException("用户不存在")
    return UserMe(
        id=user["id"],
        username=user["username"],
        email=user["email"],
        role=user["role"],
        is_active=True,
    )
