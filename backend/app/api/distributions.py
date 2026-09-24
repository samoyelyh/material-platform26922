# ============================================================================
# 派发运营 API（素材域业务链真实落地 + V1.1 RBAC 身份化）
#
#   POST /api/design-packages/{id}/distributions   派发（ADMIN / DESIGN_MANAGER，指定真实运营）
#   GET  /api/design-packages/{id}/distributions   该设计包的任务（素材侧角色）
#   GET  /api/distributions/my                     我的任务（OPERATOR 只看自己 / 管理角色看全部）
#   POST /api/distributions/{id}/receive           接收（ADMIN / DESIGN_MANAGER / 本任务运营本人）
#   POST /api/distributions/{id}/cancel            取消（仅 ADMIN / DESIGN_MANAGER）
#   PUT  /api/distributions/{id}/asins             回填 ASIN（ADMIN / DESIGN_MANAGER / 本任务运营本人）
#   GET  /api/distributions/{id}                   详情（管理角色 / 本任务运营本人；其它运营 404）
#   GET  /api/distributions/{id}/download          下载素材包（同上）
#   GET  /api/distributions                        全部任务（仅 ADMIN / DESIGN_MANAGER）
#
# 后端真实校验：OPERATOR 只能看/操作自己的任务；跨任务一律 403 / 404（不泄露他人任务）。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, require_roles
from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.core.security import ROLE_ADMIN, ROLE_DESIGN_MANAGER, ROLE_OPERATOR
from app.db.models import User
from app.db.session import get_db
from app.schemas.dto import DistributionTaskDTO
from app.services.distribution_service import (
    DistributionTaskNotFound,
    bind_asins,
    build_task_zip,
    cancel_task,
    dispatch_task,
    get_task,
    list_all_tasks,
    list_my_tasks,
    list_tasks,
    receive_task,
    to_task_dto,
)

router = APIRouter(tags=["distributions"])

DEFAULT_ACTOR = "肖芸"

MATERIAL_ROLES = (ROLE_ADMIN, ROLE_DESIGN_MANAGER)
dispatch_manager = require_roles(ROLE_ADMIN, ROLE_DESIGN_MANAGER)


class DispatchRequest(BaseModel):
    # V1.1：派发指定真实运营 user_id（不再手输名字）
    operatorUserId: str = Field(min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=2000)
    actor: str | None = Field(default=None, max_length=128)


class BindAsinsRequest(BaseModel):
    parentAsin: str = Field(min_length=1, max_length=32)
    children: list[str] = Field(min_length=1, max_length=500)
    site: str | None = Field(default=None, max_length=8)
    actor: str | None = Field(default=None, max_length=128)


def _actor(payload_actor: str | None, current_user: User) -> str:
    return (payload_actor or "").strip() or current_user.display_name or DEFAULT_ACTOR


def _is_manager(user: User) -> bool:
    return user.role in MATERIAL_ROLES


def _task_for_user(db: Session, task_id: str, current_user: User):
    """取任务：管理角色可看全部；OPERATOR 只能看自己（operator_user_id 匹配），否则 404 不泄露。"""
    from app.db.models import DistributionTask

    task = db.get(DistributionTask, task_id)
    if task is None:
        raise DistributionTaskNotFound()
    if _is_manager(current_user):
        return task
    if task.operator_user_id and task.operator_user_id == current_user.id:
        return task
    # 其它运营：不泄露他人任务存在性
    raise NotFoundError("无权查看该派发任务")


# ---------------------------------------------------------------- 派发 / 列表 / 我的任务


@router.post(
    "/design-packages/{design_package_id}/distributions",
    response_model=DistributionTaskDTO,
    summary="派发当前最新 Batch 给真实运营（ADMIN / DESIGN_MANAGER）",
)
def dispatch(
    design_package_id: str,
    payload: DispatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(dispatch_manager),
) -> DistributionTaskDTO:
    operator = db.get(User, payload.operatorUserId)
    if operator is None:
        raise ValidationError("指定的运营账号不存在")
    task = dispatch_task(
        db,
        design_package_id,
        operator_user=operator,
        actor=_actor(payload.actor, current_user),
        remark=payload.remark,
    )
    db.commit()
    return to_task_dto(db, task)


@router.get(
    "/design-packages/{design_package_id}/distributions",
    response_model=list[DistributionTaskDTO],
    summary="该设计包的全部派发任务（素材侧角色）",
)
def list_distributions(
    design_package_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(dispatch_manager),
) -> list[DistributionTaskDTO]:
    tasks = list_tasks(db, design_package_id)
    return [to_task_dto(db, task) for task in tasks]


@router.get(
    "/distributions/my",
    response_model=list[DistributionTaskDTO],
    summary="我的任务（OPERATOR 只看自己；ADMIN/DESIGN_MANAGER 看全部）",
)
def my_tasks(
    status: str | None = Query(default=None, description="ACTIVE / RECEIVED / COMPLETED / CANCELLED"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[DistributionTaskDTO]:
    if _is_manager(current_user):
        tasks = list_all_tasks(db, status=status)
    else:
        tasks = list_my_tasks(db, current_user.id, status=status)
    return [to_task_dto(db, task) for task in tasks]


@router.get(
    "/distributions",
    response_model=list[DistributionTaskDTO],
    summary="全部派发任务（仅 ADMIN / DESIGN_MANAGER）",
)
def list_distributions_global(
    status: str | None = Query(default=None, description="ACTIVE / RECEIVED / COMPLETED / CANCELLED"),
    db: Session = Depends(get_db),
    _: User = Depends(dispatch_manager),
) -> list[DistributionTaskDTO]:
    tasks = list_all_tasks(db, status=status)
    return [to_task_dto(db, task) for task in tasks]


# ---------------------------------------------------------------- 接收 / 取消 / 回填 / 详情 / 下载


@router.post(
    "/distributions/{task_id}/receive",
    response_model=DistributionTaskDTO,
    summary="接收素材（ADMIN / DESIGN_MANAGER / 本任务运营本人）",
)
def receive(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionTaskDTO:
    task = _task_for_user(db, task_id, current_user)
    task = receive_task(db, task.id, current_user.display_name or DEFAULT_ACTOR)
    db.commit()
    return to_task_dto(db, task)


@router.post(
    "/distributions/{task_id}/cancel",
    response_model=DistributionTaskDTO,
    summary="取消派发（仅 ADMIN / DESIGN_MANAGER；OPERATOR 不允许）",
)
def cancel(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(dispatch_manager),
) -> DistributionTaskDTO:
    task = cancel_task(db, task_id, current_user.display_name or DEFAULT_ACTOR)
    db.commit()
    return to_task_dto(db, task)


@router.put(
    "/distributions/{task_id}/asins",
    response_model=DistributionTaskDTO,
    summary="回填 Parent / Child ASIN（ADMIN / DESIGN_MANAGER / 本任务运营本人）",
)
def put_asins(
    task_id: str,
    payload: BindAsinsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionTaskDTO:
    task = _task_for_user(db, task_id, current_user)
    task = bind_asins(
        db,
        task.id,
        parent_asin=payload.parentAsin,
        children=payload.children,
        site=payload.site,
        actor=_actor(payload.actor, current_user),
    )
    db.commit()
    return to_task_dto(db, task)


@router.get(
    "/distributions/{task_id}",
    response_model=DistributionTaskDTO,
    summary="单个派发任务详情（管理角色 / 本任务运营本人；其它运营 404）",
)
def task_detail(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> DistributionTaskDTO:
    task = _task_for_user(db, task_id, current_user)
    return to_task_dto(db, task)


@router.get(
    "/distributions/{task_id}/download",
    summary="按派发快照下载素材包 ZIP（管理角色 / 本任务运营本人）",
    response_class=Response,
)
def download_task_zip(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    from urllib.parse import quote

    task = _task_for_user(db, task_id, current_user)
    data, filename = build_task_zip(db, task)
    quoted = quote(filename)
    # Content-Disposition 的 filename= 只能 ASCII；中文名放 filename*=UTF-8''
    ascii_fallback = f"materials-{task.version_code}-{task.id[:8]}.zip"
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{ascii_fallback}"; filename*=UTF-8\'\'{quoted}'
        },
    )
