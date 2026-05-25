# auth.py — 创作者身份认证系统
"""
知数知圈 · JWT 认证模块

提供：
  - 创作者注册 / 登录
  - JWT 访问令牌颁发与验证
  - FastAPI 依赖注入：get_current_creator

路由挂载（oracle_engine.py 末尾）：
  from auth import auth_router
  app.include_router(auth_router)
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr, field_validator

# ── 可选依赖（降级处理）─────────────────────────────────────────────
try:
    from jose import JWTError, jwt
    _JWT_OK = True
except ImportError:
    _JWT_OK = False
    print("!! [auth] python-jose 未安装，请执行: pip install python-jose[cryptography]")

try:
    from passlib.context import CryptContext
    _pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    _PASSLIB_OK = True
except ImportError:
    _PASSLIB_OK = False
    print("!! [auth] passlib 未安装，请执行: pip install passlib[bcrypt]")

import storage  # 同目录

# ── JWT 配置 ────────────────────────────────────────────────────────
_SECRET_KEY = os.environ.get(
    "JWT_SECRET_KEY",
    "zhishuzhiquan-dev-secret-please-change-in-production-2024"
)
_ALGORITHM   = "HS256"
_EXPIRE_DAYS = int(os.environ.get("JWT_EXPIRE_DAYS", "30"))

auth_router  = APIRouter(tags=["创作者认证"])
_bearer      = HTTPBearer(auto_error=False)


# ════════════════════════════════════════════════════════════════════
# 密码工具
# ════════════════════════════════════════════════════════════════════

def _hash_password(plain: str) -> str:
    if _PASSLIB_OK:
        return _pwd_ctx.hash(plain)
    # 降级：简单 sha256（仅开发调试，生产必须装 passlib）
    import hashlib
    return "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


def _verify_password(plain: str, hashed: str) -> bool:
    if _PASSLIB_OK and not hashed.startswith("sha256:"):
        return _pwd_ctx.verify(plain, hashed)
    import hashlib
    return hashed == "sha256:" + hashlib.sha256(plain.encode()).hexdigest()


# ════════════════════════════════════════════════════════════════════
# JWT 工具
# ════════════════════════════════════════════════════════════════════

def create_access_token(creator_id: str, username: str) -> str:
    if not _JWT_OK:
        # 降级：返回明文 base64（仅开发）
        import base64, json
        payload = {"sub": creator_id, "username": username}
        return base64.b64encode(json.dumps(payload).encode()).decode()

    expire = datetime.utcnow() + timedelta(days=_EXPIRE_DAYS)
    payload = {
        "sub":      creator_id,
        "username": username,
        "exp":      expire,
        "iat":      datetime.utcnow(),
    }
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解码 JWT，失败返回 None。"""
    if not _JWT_OK:
        import base64, json
        try:
            return json.loads(base64.b64decode(token).decode())
        except Exception:
            return None
    try:
        return jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
    except JWTError:
        return None


# ════════════════════════════════════════════════════════════════════
# FastAPI 依赖
# ════════════════════════════════════════════════════════════════════

def get_current_creator(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> dict:
    """
    解析请求头中的 Bearer Token，返回创作者信息 dict。
    用法：在路由中声明 creator: dict = Depends(get_current_creator)
    """
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录，缺少 Authorization 头",
        )
    payload = decode_token(creds.credentials)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
        )
    creator = storage.get_creator_by_id(payload["sub"])
    if creator is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="创作者账户不存在",
        )
    return creator


def get_optional_creator(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Optional[dict]:
    """可选认证：有 Token 则解析，无 Token 返回 None（不报错）。"""
    if creds is None:
        return None
    payload = decode_token(creds.credentials)
    if payload is None:
        return None
    return storage.get_creator_by_id(payload.get("sub", ""))


# ════════════════════════════════════════════════════════════════════
# 请求 / 响应模型
# ════════════════════════════════════════════════════════════════════

class RegisterRequest(BaseModel):
    username:     str
    password:     str
    display_name: str = ""
    email:        str = ""

    @field_validator("username")
    @classmethod
    def username_valid(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2:
            raise ValueError("用户名至少 2 个字符")
        if len(v) > 32:
            raise ValueError("用户名最多 32 个字符")
        return v

    @field_validator("password")
    @classmethod
    def password_valid(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码至少 6 位")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    creator_id:   str
    username:     str
    display_name: str


# ════════════════════════════════════════════════════════════════════
# 路由
# ════════════════════════════════════════════════════════════════════

@auth_router.post("/api/auth/register", response_model=TokenResponse, summary="创作者注册")
async def register(req: RegisterRequest):
    """注册新创作者账户，成功后直接返回 Token（免二次登录）。"""
    existing = storage.get_creator_by_username(req.username)
    if existing:
        raise HTTPException(400, f"用户名 '{req.username}' 已被占用")

    creator_id = str(uuid.uuid4())
    password_hash = _hash_password(req.password)
    display_name = req.display_name.strip() or req.username

    storage.save_creator(
        creator_id=creator_id,
        username=req.username,
        email=req.email,
        password_hash=password_hash,
        display_name=display_name,
    )

    token = create_access_token(creator_id, req.username)
    return TokenResponse(
        access_token=token,
        creator_id=creator_id,
        username=req.username,
        display_name=display_name,
    )


@auth_router.post("/api/auth/login", response_model=TokenResponse, summary="创作者登录")
async def login(req: LoginRequest):
    """用用户名 + 密码换取 JWT Token。"""
    creator = storage.get_creator_by_username(req.username)
    if not creator or not _verify_password(req.password, creator["password_hash"]):
        raise HTTPException(401, "用户名或密码错误")

    token = create_access_token(creator["creator_id"], creator["username"])
    return TokenResponse(
        access_token=token,
        creator_id=creator["creator_id"],
        username=creator["username"],
        display_name=creator["display_name"],
    )


@auth_router.get("/api/auth/me", summary="获取当前登录创作者信息")
async def me(creator: dict = Depends(get_current_creator)):
    """验证 Token 并返回当前创作者的基本信息。"""
    return {
        "creator_id":   creator["creator_id"],
        "username":     creator["username"],
        "display_name": creator["display_name"],
        "email":        creator["email"],
        "created_at":   creator["created_at"],
    }
