# ============================================================================
# 认证依赖（get_current_user）+ 角色守卫（require_roles）
#
# 后端真实校验：不只是前端隐藏按钮。
#   - 401：未登录 / token 无效 / 过期 / 用户不存在
#   - 403：已登录但角色不符 / 用户被禁用
# 每次请求都以数据库用户状态为准（is_active 实时校验）。
# ============================================================================

from __future__ import annotations

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.security import decode_access_token
from app.db.models import User
from app.db.session import get_db


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise UnauthorizedError("缺少 Authorization 头")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise UnauthorizedError("Authorization 头格式应为 Bearer <token>")
    return parts[1].strip()


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    """解析 Bearer token → 数据库用户（实时校验存在 + is_active）。"""
    token = _bearer_token(authorization)
    payload = decode_access_token(token)
    if payload is None:
        raise UnauthorizedError("登录已失效，请重新登录")
    user_id = payload.get("sub")
    if not user_id:
        raise UnauthorizedError("登录已失效，请重新登录")
    user = db.get(User, user_id)
    if user is None:
        raise UnauthorizedError("账号不存在，请重新登录")
    if not user.is_active:
        raise ForbiddenError("账号已被禁用，请联系管理员")
    return user


def require_roles(*roles: str):
    """角色守卫工厂：current_user.role 必须在 roles 内，否则 403。"""

    def _guard(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise ForbiddenError(
                f"当前角色（{current_user.role}）无权执行该操作"
            )
        return current_user

    return _guard


def list_operators(db: Session) -> list[User]:
    """可派发的运营（role=OPERATOR 且启用）。"""
    from app.core.security import ROLE_OPERATOR

    return list(
        db.execute(
            select(User)
            .where(User.role == ROLE_OPERATOR, User.is_active.is_(True))
            .order_by(User.display_name.asc())
        ).scalars()
    )


__all__ = ["get_current_user", "list_operators", "require_roles"]
