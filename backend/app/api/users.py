# ============================================================================
# 用户管理 API（V1.1，仅 ADMIN）+ 运营选项列表
#
#   GET    /api/users               用户列表（ADMIN）
#   POST   /api/users               新增用户（ADMIN）
#   PATCH  /api/users/{id}          修改 姓名/角色/状态/重置密码（ADMIN）
#   GET    /api/users/operators     可派发运营（role=OPERATOR 且启用；派发选择器用）
#
# 安全：绝不返回 password_hash；离职用户 is_active=false（不物理删除）。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, list_operators, require_roles
from app.core.errors import NotFoundError, ValidationError
from app.core.security import (
    ALL_ROLES,
    ROLE_ADMIN,
    hash_password,
)
from app.db.models import User
from app.db.session import get_db
from app.schemas.dto import OperatorOptionDTO, UserCreateRequest, UserDTO, UserPatchRequest
from app.services.repository import new_id, utcnow

router = APIRouter(tags=["users"])


class UserNotFound(NotFoundError):
    code = "USER_NOT_FOUND"
    default_message = "用户不存在"


admin = require_roles(ROLE_ADMIN)


@router.get("/users", response_model=list[UserDTO], summary="用户列表（ADMIN）")
def list_users(
    db: Session = Depends(get_db),
    _: User = Depends(admin),
) -> list[UserDTO]:
    rows = db.execute(select(User).order_by(User.created_at.asc())).scalars().all()
    return [UserDTO.from_entity(row) for row in rows]


@router.get(
    "/users/operators",
    response_model=list[OperatorOptionDTO],
    summary="可派发运营选项（role=OPERATOR 且启用）",
)
def get_operators(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[OperatorOptionDTO]:
    rows = list_operators(db)
    return [
        OperatorOptionDTO(id=row.id, username=row.username, displayName=row.display_name)
        for row in rows
    ]


@router.post("/users", response_model=UserDTO, summary="新增用户（ADMIN）")
def create_user(
    payload: UserCreateRequest,
    db: Session = Depends(get_db),
    _: User = Depends(admin),
) -> UserDTO:
    username = payload.username.strip()
    if db.execute(select(User.id).where(User.username == username)).scalar_one_or_none() is not None:
        raise ValidationError(f"账号 {username} 已存在")
    if payload.role not in ALL_ROLES:
        raise ValidationError(f"非法角色：{payload.role}")
    user = User(
        id=new_id("user"),
        username=username,
        password_hash=hash_password(payload.password),
        display_name=payload.displayName.strip(),
        role=payload.role,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserDTO.from_entity(user)


@router.patch("/users/{user_id}", response_model=UserDTO, summary="修改用户 姓名/角色/状态/重置密码（ADMIN）")
def patch_user(
    user_id: str,
    payload: UserPatchRequest,
    db: Session = Depends(get_db),
    _: User = Depends(admin),
) -> UserDTO:
    user = db.get(User, user_id)
    if user is None:
        raise UserNotFound()
    if payload.displayName is not None:
        user.display_name = payload.displayName.strip()
    if payload.role is not None:
        if payload.role not in ALL_ROLES:
            raise ValidationError(f"非法角色：{payload.role}")
        user.role = payload.role
    if payload.isActive is not None:
        user.is_active = payload.isActive
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    user.updated_at = utcnow()
    db.commit()
    db.refresh(user)
    return UserDTO.from_entity(user)
