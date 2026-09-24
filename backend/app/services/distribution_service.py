# ============================================================================
# 派发运营（DistributionTask）服务
#
# 业务链：DesignPackage → DerivativeBatch → DistributionTask → 运营接收 →
# Parent ASIN / Child ASIN。
#
# 前端此前把这条链只放在内存态（workflowStore.tasks）。这里落成真实素材域表，
# 使对 order-center 的只读契约（Child ASIN → Batch → Variant 候选）基于真实数据。
#
# 快照规则（与前端 makeDistributionItems 一致）：
#   派发时把「副素材 + 当时 Revision」快照进 distribution_task_items；
#   之后副素材 Revision 升级不改变历史派发交付内容。
# ============================================================================

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import (
    BatchNotFound,
    DesignPackageNotFound,
    NotFoundError,
    ValidationError,
)
from app.db.models import (
    Asset,
    DerivativeBatch,
    DesignPackage,
    DistributionChildAsin,
    DistributionParentAsin,
    DistributionTask,
    DistributionTaskItem,
    MaterialPairing,
    MaterialVariant,
    User,
    VariantRevision,
)
from app.schemas.dto import (
    ChildAsinDTO,
    DistributionTaskDTO,
    DistributionTaskItemDTO,
    ParentAsinDTO,
)
from app.services.activity import write_log
from app.services.repository import new_id, utcnow

DEFAULT_ACTOR = "肖芸"


class DistributionTaskNotFound(NotFoundError):
    code = "DISTRIBUTION_TASK_NOT_FOUND"
    default_message = "派发任务不存在"


class ActiveDistributionConflict(ValidationError):
    code = "DISTRIBUTION_ALREADY_ACTIVE"
    default_message = "该版本已派发给该运营，存在进行中的任务"


# ---------------------------------------------------------------- 写操作


def dispatch_task(
    db: Session,
    design_package_id: str,
    *,
    operator_user: User,
    actor: str,
    remark: str | None = None,
) -> DistributionTask:
    """
    把一个设计包的「当前最新 Batch」及其整套副素材派发给真实运营用户。
    operator_user_id / operator_id(username) / operator_name(display_name) 全部来自该用户。
    同一 (batch, operator) 不允许存在两个进行中（ACTIVE/RECEIVED）的任务。
    """
    from app.core.security import ROLE_OPERATOR

    if operator_user.role != ROLE_OPERATOR or not operator_user.is_active:
        raise ValidationError("只能派发给启用状态的运营账号（OPERATOR）")

    package = db.get(DesignPackage, design_package_id)
    if package is None:
        raise DesignPackageNotFound()

    batch = (
        db.execute(
            select(DerivativeBatch)
            .where(DerivativeBatch.design_package_id == design_package_id)
            .order_by(DerivativeBatch.version_no.desc())
            .limit(1)
        ).scalar_one_or_none()
    )
    if batch is None:
        raise BatchNotFound("该设计包还没有上架版本，无法派发")

    operator_id = operator_user.username
    operator_name = operator_user.display_name

    active = (
        db.execute(
            select(DistributionTask.id)
            .where(
                DistributionTask.batch_id == batch.id,
                DistributionTask.operator_id == operator_id,
                DistributionTask.status.in_(["ACTIVE", "RECEIVED"]),
            )
            .limit(1)
        ).scalar_one_or_none()
    )
    if active is not None:
        raise ActiveDistributionConflict()

    task = DistributionTask(
        id=new_id("task"),
        design_package_id=design_package_id,
        batch_id=batch.id,
        package_name=package.name,
        package_code=package.code,
        version_code=batch.code,
        designer_name=package.designer_name,
        operator_id=operator_id,
        operator_name=operator_name,
        operator_user_id=operator_user.id,
        status="ACTIVE",
        assigned_at=utcnow(),
        remark=remark,
    )
    db.add(task)
    db.flush()

    # 快照：本 Batch 实际使用的整套副素材（新建 + 复用来的）
    variants = _batch_variant_entities(db, batch)
    for variant in variants:
        if not variant.current_revision_id:
            continue
        db.add(
            DistributionTaskItem(
                id=new_id("taskitem"),
                distribution_task_id=task.id,
                variant_id=variant.id,
                revision_id=variant.current_revision_id,
                delivery_round=1,
            )
        )
    db.flush()

    write_log(
        db,
        design_package_id=design_package_id,
        target_type="DISTRIBUTION",
        target_id=task.id,
        actor=actor,
        action="DISPATCH",
        summary=(
            f"派发给 {operator_name}，版本 {batch.code}，"
            f"第 1 次交付 {len(variants)} 张副素材（复用同一套底层素材，不复制图片）"
        ),
        after_value=batch.code,
    )
    return task


def receive_task(db: Session, task_id: str, actor: str) -> DistributionTask:
    task = db.get(DistributionTask, task_id)
    if task is None:
        raise DistributionTaskNotFound()
    if task.status == "ACTIVE":
        task.status = "RECEIVED"
        task.received_at = utcnow()
        db.flush()
        write_log(
            db,
            design_package_id=task.design_package_id,
            target_type="DISTRIBUTION",
            target_id=task.id,
            actor=actor,
            action="RECEIVE",
            summary=f"接收素材（版本 {task.version_code}，{len(task.items)} 项交付）",
            after_value=task.version_code,
        )
    return task


def cancel_task(db: Session, task_id: str, actor: str) -> DistributionTask:
    task = db.get(DistributionTask, task_id)
    if task is None:
        raise DistributionTaskNotFound()
    if task.status != "CANCELLED":
        task.status = "CANCELLED"
        task.cancelled_at = utcnow()
        db.flush()
        write_log(
            db,
            design_package_id=task.design_package_id,
            target_type="DISTRIBUTION",
            target_id=task.id,
            actor=actor,
            action="CANCEL_DISTRIBUTION",
            summary=f"取消派发给 {task.operator_name}（版本 {task.version_code}）",
            after_value=task.version_code,
        )
    return task


def bind_asins(
    db: Session,
    task_id: str,
    *,
    parent_asin: str,
    children: list[str],
    site: str | None = None,
    actor: str,
) -> DistributionTask:
    """运营回填 Parent / Child ASIN。Parent 本身即唯一标识；Child 默认共享整套素材。"""
    task = db.get(DistributionTask, task_id)
    if task is None:
        raise DistributionTaskNotFound()

    parent = (parent_asin or "").strip().upper()
    if not parent:
        raise ValidationError("Parent ASIN 不能为空")
    _validate_asin(parent, "Parent")

    cleaned: list[str] = []
    for child in children:
        c = (child or "").strip().upper()
        if not c:
            continue
        _validate_asin(c, "Child")
        if c not in cleaned:
            cleaned.append(c)
    if not cleaned:
        raise ValidationError("请至少提供 1 个有效的 Child ASIN")

    # Parent：一个任务一个（覆盖式更新）
    parent_row = (
        db.execute(
            select(DistributionParentAsin).where(
                DistributionParentAsin.distribution_task_id == task.id
            )
        ).scalar_one_or_none()
    )
    if parent_row is None:
        parent_row = DistributionParentAsin(
            id=new_id("pasin"),
            distribution_task_id=task.id,
            parent_asin=parent,
            site=site,
        )
        db.add(parent_row)
    else:
        parent_row.parent_asin = parent
        parent_row.site = site
        parent_row.listing_url = None
    db.flush()

    # Child：按 asin 覆盖式同步
    existing = list(
        db.execute(
            select(DistributionChildAsin).where(
                DistributionChildAsin.distribution_task_id == task.id
            )
        ).scalars()
    )
    existing_by_asin = {row.child_asin: row for row in existing}
    wanted = set(cleaned)
    for row in existing:
        if row.child_asin not in wanted:
            db.delete(row)
    for child in cleaned:
        row = existing_by_asin.get(child)
        if row is None:
            db.add(
                DistributionChildAsin(
                    id=new_id("casin"),
                    distribution_task_id=task.id,
                    child_asin=child,
                    site=site,
                )
            )
        else:
            row.site = site
    db.flush()

    if task.status not in ("COMPLETED", "CANCELLED"):
        task.status = "COMPLETED"
        task.completed_at = utcnow()
        db.flush()

    write_log(
        db,
        design_package_id=task.design_package_id,
        target_type="PARENT_ASIN",
        target_id=parent,
        actor=actor,
        action="PARENT_ASIN",
        summary=f"回填 Parent ASIN {parent}，关联 Child ASIN {len(cleaned)} 个",
        after_value=parent,
    )
    return task


def _validate_asin(asin: str, label: str) -> None:
    if not _is_valid_asin(asin):
        raise ValidationError(f"{label} ASIN 格式应为 B0 + 8 位字母或数字：{asin}")


def _is_valid_asin(asin: str) -> bool:
    import re

    return bool(re.fullmatch(r"B0[A-Z0-9]{8}", asin))


# ---------------------------------------------------------------- 查询 / DTO


def _batch_variant_entities(db: Session, batch: DerivativeBatch) -> list[MaterialVariant]:
    """一个 Batch 实际使用的整套副素材（本版本新建的 + 通过配对复用来的）。"""
    created = list(
        db.execute(select(MaterialVariant).where(MaterialVariant.batch_id == batch.id)).scalars()
    )
    reused: list[MaterialVariant] = []
    if batch.created_from_upload_id:
        reused = list(
            db.execute(
                select(MaterialVariant)
                .join(MaterialPairing, MaterialPairing.variant_id == MaterialVariant.id)
                .where(
                    MaterialPairing.package_upload_id == batch.created_from_upload_id,
                    MaterialVariant.batch_id != batch.id,
                )
            ).scalars()
        )
    seen: set[str] = set()
    merged: list[MaterialVariant] = []
    for variant in [*created, *reused]:
        if variant.id not in seen:
            seen.add(variant.id)
            merged.append(variant)
    merged.sort(key=lambda v: v.created_at)
    return merged


def get_task(db: Session, task_id: str) -> DistributionTask:
    task = db.get(DistributionTask, task_id)
    if task is None:
        raise DistributionTaskNotFound()
    return task


def list_tasks(db: Session, design_package_id: str) -> list[DistributionTask]:
    return list(
        db.execute(
            select(DistributionTask)
            .where(DistributionTask.design_package_id == design_package_id)
            .order_by(DistributionTask.created_at.asc())
        ).scalars()
    )


def to_task_dto(db: Session, task: DistributionTask) -> DistributionTaskDTO:
    items = sorted(task.items, key=lambda it: it.created_at)
    children = sorted(task.children, key=lambda c: c.child_asin)
    delivery_round = max((it.delivery_round for it in items), default=0)
    return DistributionTaskDTO(
        id=task.id,
        designPackageId=task.design_package_id,
        batchId=task.batch_id,
        packageName=task.package_name,
        packageCode=task.package_code,
        versionCode=task.version_code,
        designerName=task.designer_name,
        operatorId=task.operator_id,
        operatorName=task.operator_name,
        operatorUserId=task.operator_user_id,
        status=task.status,
        assignedAt=task.assigned_at,
        receivedAt=task.received_at,
        completedAt=task.completed_at,
        cancelledAt=task.cancelled_at,
        remark=task.remark,
        items=[DistributionTaskItemDTO.from_entity(it) for it in items],
        parentAsin=ParentAsinDTO.from_entity(task.parent) if task.parent else None,
        children=[ChildAsinDTO.from_entity(c) for c in children],
        variantCount=len(items),
        deliveryRound=delivery_round,
    )


def list_all_tasks(db: Session, status: str | None = None) -> list[DistributionTask]:
    """全部派发任务（分发任务列表用），可按状态过滤。"""
    stmt = select(DistributionTask)
    if status:
        stmt = stmt.where(DistributionTask.status == status)
    stmt = stmt.order_by(DistributionTask.assigned_at.desc())
    return list(db.execute(stmt).scalars())


def list_my_tasks(db: Session, operator_user_id: str, status: str | None = None) -> list[DistributionTask]:
    """「我的任务」：只返回派给当前运营用户的任务（后端过滤，OPERATOR 只能看自己）。"""
    stmt = select(DistributionTask).where(
        DistributionTask.operator_user_id == operator_user_id
    )
    if status:
        stmt = stmt.where(DistributionTask.status == status)
    stmt = stmt.order_by(DistributionTask.assigned_at.desc())
    return list(db.execute(stmt).scalars())


def build_task_zip(db: Session, task: DistributionTask) -> tuple[bytes, str]:
    """
    按 DistributionTask 的**派发快照**（distribution_task_items 里的 variant + revision）
    打包副素材为 ZIP，返回 (bytes, 建议文件名)。

    - 必须读快照里的 revision_id（不是当前 Revision）：后续 Variant 被修改，
      历史派发任务下载到的仍是派发当时的那张图。
    - 文件名：{设计包名}_{版本}_{运营}_{taskId}.zip
    - ZIP 内条目：{displayCode}.{ext}，重复 displayCode 追加序号（跨交付轮次去重）。
    - 读文件失败跳过该条（不影响其它文件），避免单个坏文件阻塞整包下载。
    """
    import io
    import zipfile

    revision_ids = {it.revision_id for it in task.items}
    revisions = {
        row.id: row
        for row in db.execute(
            select(VariantRevision).where(VariantRevision.id.in_(revision_ids))
        ).scalars()
    } if revision_ids else {}
    asset_ids = {r.asset_id for r in revisions.values()}
    assets = {
        row.id: row
        for row in db.execute(select(Asset).where(Asset.id.in_(asset_ids))).scalars()
    } if asset_ids else {}

    variant_ids = {it.variant_id for it in task.items}
    variants = {
        row.id: row
        for row in db.execute(select(MaterialVariant).where(MaterialVariant.id.in_(variant_ids))).scalars()
    } if variant_ids else {}

    from app.services.storage import get_storage

    storage = get_storage()
    buf = io.BytesIO()
    used_names: set[str] = set()
    added = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in sorted(task.items, key=lambda it: (it.delivery_round, it.created_at)):
            revision = revisions.get(item.revision_id)
            if revision is None:
                continue
            asset = assets.get(revision.asset_id)
            if asset is None:
                continue
            variant = variants.get(item.variant_id)
            display_code = variant.display_code if variant else item.variant_id[:8]
            ext = asset.storage_key.rsplit(".", 1)[-1] if "." in asset.storage_key else "jpg"
            base = f"{display_code}.{ext}"
            name = base
            index = 2
            while name in used_names:
                name = f"{display_code}-{index}.{ext}"
                index += 1
            used_names.add(name)
            try:
                data, _mime = storage.get(asset.storage_key)
            except Exception:  # noqa: BLE001 - 单文件失败不阻塞整包
                continue
            zf.writestr(name, data)
            added += 1

    safe_pkg = "".join(ch for ch in task.package_name if ch.isalnum() or ch in "-_")[:20] or "pkg"
    safe_op = "".join(ch for ch in task.operator_name if ch.isalnum() or ch in "-_")[:12] or "op"
    filename = f"{safe_pkg}_{task.version_code}_{safe_op}_{task.id[:8]}.zip"
    return buf.getvalue(), filename


__all__ = [
    "ActiveDistributionConflict",
    "DistributionTaskNotFound",
    "bind_asins",
    "build_task_zip",
    "cancel_task",
    "dispatch_task",
    "get_task",
    "list_all_tasks",
    "list_my_tasks",
    "list_tasks",
    "receive_task",
    "to_task_dto",
]
