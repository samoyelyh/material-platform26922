# ============================================================================
# 派发运营 API（素材域业务链真实落地）
#
#   POST /api/design-packages/{id}/distributions   派发（当前最新 Batch → operator）
#   GET  /api/design-packages/{id}/distributions   该设计包的全部派发任务
#   POST /api/distributions/{id}/receive           运营接收
#   POST /api/distributions/{id}/cancel            取消派发
#   PUT  /api/distributions/{id}/asins             回填 Parent / Child ASIN
#   GET  /api/distributions/{id}                   单个派发任务详情
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.dto import DistributionTaskDTO
from app.services.distribution_service import (
    bind_asins,
    build_task_zip,
    cancel_task,
    dispatch_task,
    get_task,
    list_all_tasks,
    list_tasks,
    receive_task,
    to_task_dto,
)

router = APIRouter(tags=["distributions"])

DEFAULT_ACTOR = "肖芸"


class DispatchRequest(BaseModel):
    operatorId: str | None = Field(default=None, max_length=64)
    operatorName: str | None = Field(default=None, max_length=128)
    remark: str | None = Field(default=None, max_length=2000)
    actor: str | None = Field(default=None, max_length=128)


class BindAsinsRequest(BaseModel):
    parentAsin: str = Field(min_length=1, max_length=32)
    children: list[str] = Field(min_length=1, max_length=500)
    site: str | None = Field(default=None, max_length=8)
    actor: str | None = Field(default=None, max_length=128)


def _actor(payload_actor: str | None) -> str:
    return (payload_actor or "").strip() or DEFAULT_ACTOR


@router.post(
    "/design-packages/{design_package_id}/distributions",
    response_model=DistributionTaskDTO,
    summary="派发当前最新 Batch 给运营（含整套副素材快照）",
)
def dispatch(
    design_package_id: str,
    payload: DispatchRequest,
    db: Session = Depends(get_db),
) -> DistributionTaskDTO:
    task = dispatch_task(
        db,
        design_package_id,
        operator_id=payload.operatorId or "",
        operator_name=payload.operatorName or "",
        actor=_actor(payload.actor),
        remark=payload.remark,
    )
    db.commit()
    return to_task_dto(db, task)


@router.get(
    "/design-packages/{design_package_id}/distributions",
    response_model=list[DistributionTaskDTO],
    summary="该设计包的全部派发任务",
)
def list_distributions(
    design_package_id: str, db: Session = Depends(get_db)
) -> list[DistributionTaskDTO]:
    tasks = list_tasks(db, design_package_id)
    return [to_task_dto(db, task) for task in tasks]


@router.post(
    "/distributions/{task_id}/receive",
    response_model=DistributionTaskDTO,
    summary="运营接收素材",
)
def receive(task_id: str, db: Session = Depends(get_db)) -> DistributionTaskDTO:
    task = receive_task(db, task_id, DEFAULT_ACTOR)
    db.commit()
    return to_task_dto(db, task)


@router.post(
    "/distributions/{task_id}/cancel",
    response_model=DistributionTaskDTO,
    summary="取消派发",
)
def cancel(task_id: str, db: Session = Depends(get_db)) -> DistributionTaskDTO:
    task = cancel_task(db, task_id, DEFAULT_ACTOR)
    db.commit()
    return to_task_dto(db, task)


@router.put(
    "/distributions/{task_id}/asins",
    response_model=DistributionTaskDTO,
    summary="运营回填 Parent / Child ASIN",
)
def put_asins(
    task_id: str,
    payload: BindAsinsRequest,
    db: Session = Depends(get_db),
) -> DistributionTaskDTO:
    task = bind_asins(
        db,
        task_id,
        parent_asin=payload.parentAsin,
        children=payload.children,
        site=payload.site,
        actor=_actor(payload.actor),
    )
    db.commit()
    return to_task_dto(db, task)


@router.get(
    "/distributions/{task_id}",
    response_model=DistributionTaskDTO,
    summary="单个派发任务详情",
)
def task_detail(task_id: str, db: Session = Depends(get_db)) -> DistributionTaskDTO:
    task = get_task(db, task_id)
    return to_task_dto(db, task)


@router.get(
    "/distributions",
    response_model=list[DistributionTaskDTO],
    summary="全部派发任务（分发列表，可按状态筛选）",
)
def list_distributions_global(
    status: str | None = Query(default=None, description="ACTIVE / RECEIVED / COMPLETED / CANCELLED"),
    db: Session = Depends(get_db),
) -> list[DistributionTaskDTO]:
    tasks = list_all_tasks(db, status=status)
    return [to_task_dto(db, task) for task in tasks]


@router.get(
    "/distributions/{task_id}/download",
    summary="按派发快照下载素材包 ZIP",
    description=(
        "按 DistributionTask 的派发快照（当时 variant + revision）打包副素材，"
        "后续 Variant 被修改不会影响历史派发任务的下载内容。"
        "文件名：{设计包名}_{版本}_{运营}_{taskId}.zip"
    ),
    response_class=Response,
)
def download_task_zip(task_id: str, db: Session = Depends(get_db)) -> Response:
    from urllib.parse import quote

    task = get_task(db, task_id)
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
