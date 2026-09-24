# ============================================================================
# 认证 API（V1.1）
#
#   POST /api/auth/login    账号 + 密码 → JWT + 用户信息
#   GET  /api/auth/me       当前登录用户（恢复登录态）
#   POST /api/auth/logout   登出（纯 JWT：前端删除 token；这里仅记录，可选）
#
# 安全：
#   - 登录失败统一「账号或密码错误」（不区分账号是否存在）
#   - 密码只校验 bcrypt 哈希，不打印
#   - is_active=false 不能登录
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.errors import UnauthorizedError
from app.core.security import create_access_token, verify_password
from app.db.models import User
from app.db.session import get_db
from app.schemas.dto import LoginRequest, LoginResponse, UserDTO
from app.services.repository import utcnow

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse, summary="账号密码登录，返回 JWT")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.execute(
        select(User).where(User.username == payload.username.strip())
    ).scalar_one_or_none()
    # 统一「账号或密码错误」，不泄露账号是否存在
    if user is None or not verify_password(payload.password, user.password_hash):
        raise UnauthorizedError("账号或密码错误")
    if not user.is_active:
        raise UnauthorizedError("账号已被禁用，请联系管理员")

    user.last_login_at = utcnow()
    db.commit()
    db.refresh(user)
    token = create_access_token(user.id, user.role)
    return LoginResponse(token=token, user=UserDTO.from_entity(user))


@router.get("/auth/me", response_model=UserDTO, summary="当前登录用户")
def me(current_user: User = Depends(get_current_user)) -> UserDTO:
    return UserDTO.from_entity(current_user)


@router.post("/auth/logout", summary="登出（纯 JWT：前端删除 token）")
def logout() -> dict:
    # 纯 JWT 无服务端会话：登出即前端删除 token。这里仅作为统一入口（可选）。
    return {"ok": True}
