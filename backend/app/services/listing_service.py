# ============================================================================
# 上架 Listing 服务
#
# 业务原则（本轮调整）：
#   - Listing URL 是上架记录核心；Parent ASIN 是后补属性（可空 → 后补更新本行，不建第二条）
#   - 一条 Listing 挂多个素材（MAT + Variant）；一个素材被多个 Listing 使用（M2M）
#   - 一个 DistributionTask 可产生多个 Listing
#   - Child ASIN 不参与素材归属（不写入 distribution_child_asins）
#   - URL 轻量标准化（trim + 去追踪参数），不做 Amazon 爬虫
# ============================================================================

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationError
from app.db.models import (
    DistributionParentAsin,
    DistributionTask,
    DistributionTaskItem,
    Listing,
    ListingMaterial,
    Material,
    MaterialVariant,
)
from app.services.activity import write_log
from app.services.distribution_service import (
    DistributionTaskNotFound,
    _is_valid_asin,
)
from app.services.repository import new_id, utcnow

# URL 中明显是流量追踪的 query 参数（命中即剔除，其余保留 canonical）
_TRACKING_PARAM_PREFIXES = (
    "pd_rd_",
    "pd_rpl_",
    "pd_page_",
    "smid",
    "ref",
    "tag",
    "th",
    "psc",
    "qid",
    "sr",
    "crid",
    "spLa",
    "sp_csd",
    "gclid",
    "fbclid",
    "utm_",
)
_TRACKING_PARAM_EXACT = {"keywords", "maas", "ref_", "linkCode", "ascsubtag"}


def normalize_listing_url(url: str) -> str:
    """轻量标准化：trim → 校验 http(s) → 去追踪 query 参数 → 保留 canonical URL。"""
    raw = (url or "").strip()
    if not raw:
        raise ValidationError("Listing URL 不能为空")
    if len(raw) > 2048:
        raise ValidationError("Listing URL 超长（>2048）")
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError("Listing URL 必须以 http:// 或 https:// 开头，例如 https://www.amazon.com/dp/B0XXXX")

    kept = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key in _TRACKING_PARAM_EXACT:
            continue
        if any(key.startswith(p) for p in _TRACKING_PARAM_PREFIXES):
            continue
        kept.append((key, value))
    query = urlencode(kept)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _validate_parent_asin(value: str | None) -> str | None:
    if value is None or (value or "").strip() == "":
        return None
    asin = value.strip().upper()
    if not _is_valid_asin(asin):
        raise ValidationError(f"Parent ASIN 格式应为 B0 + 8 位字母或数字：{value}")
    return asin


# ---------------------------------------------------------------- 创建 / 查重复用


def link_task_materials(db: Session, listing: Listing, task: DistributionTask, actor: str) -> int:
    """把任务的素材范围（快照 items 的全部副素材 + 各自 MAT）挂到 Listing。

    幂等：已存在的关联跳过。返回新建关联数。
    """
    variant_ids = {it.variant_id for it in task.items}
    material_ids = set()
    if variant_ids:
        material_ids = set(
            db.execute(
                select(MaterialVariant.material_id).where(MaterialVariant.id.in_(variant_ids))
            ).scalars()
        )

    existing = {
        (row.material_id, row.variant_id)
        for row in db.execute(
            select(ListingMaterial).where(ListingMaterial.listing_id == listing.id)
        ).scalars()
    }
    linked = 0
    for variant_id in variant_ids:
        if (None, variant_id) in existing:
            continue
        db.add(ListingMaterial(id=new_id("lmat"), listing_id=listing.id, variant_id=variant_id))
        linked += 1
    for material_id in material_ids:
        if (material_id, None) in existing:
            continue
        db.add(ListingMaterial(id=new_id("lmat"), listing_id=listing.id, material_id=material_id))
        linked += 1
    if linked:
        db.flush()
        write_log(
            db,
            design_package_id=task.design_package_id,
            target_type="LISTING",
            target_id=listing.id,
            actor=actor,
            action="LISTING_MATERIAL_LINKED",
            summary=f"Listing 自动关联任务素材：副素材 {len(variant_ids)} 个、主素材 {len(material_ids)} 个",
        )
    return linked


def create_listing(
    db: Session,
    task: DistributionTask,
    *,
    listing_url: str,
    parent_asin: str | None,
    store: str | None,
    site: str | None,
    actor_name: str,
    operator_user_id: str | None,
) -> tuple[Listing, bool]:
    """创建 Listing（Parent ASIN 可空）。同一 URL 已存在 → 不重复造行，返回已有 Listing 并自动关联当前任务素材。

    返回 (listing, created)。
    """
    url = normalize_listing_url(listing_url)
    parent = _validate_parent_asin(parent_asin)

    existing = db.execute(
        select(Listing).where(Listing.listing_url == url)
    ).scalar_one_or_none()
    if existing is not None:
        # 防重复：该链接已经存在 → 关联现有 Listing（把当前任务素材挂上去），不建第二条
        linked = link_task_materials(db, existing, task, actor_name)
        if linked:
            db.flush()
        return existing, False

    listing = Listing(
        id=new_id("listing"),
        distribution_task_id=task.id,
        listing_url=url,
        parent_asin=parent,
        parent_asin_bound_at=utcnow() if parent else None,
        store=(store or "").strip() or None,
        site=(site or "").strip() or None,
        operator_user_id=operator_user_id,
        created_by=actor_name,
    )
    db.add(listing)
    db.flush()
    link_task_materials(db, listing, task, actor_name)
    write_log(
        db,
        design_package_id=task.design_package_id,
        target_type="LISTING",
        target_id=listing.id,
        actor=actor_name,
        action="LISTING_CREATED",
        summary=f"新增上架链接{('（Parent ' + parent + '）') if parent else '（Parent ASIN 待补充）'}",
        after_value=url,
    )
    _mark_task_listed(db, task, actor_name)
    return listing, True


def _mark_task_listed(db: Session, task: DistributionTask, actor: str) -> None:
    """任务产生上架记录 → 标记 COMPLETED（上架完成）。已有终态不覆盖。"""
    if task.status not in ("COMPLETED", "CANCELLED"):
        task.status = "COMPLETED"
        task.completed_at = utcnow()
        db.flush()
        write_log(
            db,
            design_package_id=task.design_package_id,
            target_type="DISTRIBUTION",
            target_id=task.id,
            actor=actor,
            action="LISTING_CREATED",
            summary="任务已有上架链接，标记为上架完成",
        )


# ---------------------------------------------------------------- 修改 / 补 Parent ASIN


def get_listing(db: Session, listing_id: str) -> Listing:
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise ListingNotFound()
    return listing


class ListingNotFound(NotFoundError):
    code = "LISTING_NOT_FOUND"
    default_message = "上架链接不存在"


def get_listing_for_user(db: Session, listing_id: str, current_user) -> Listing:
    """权限沿用 Distribution：管理角色可访问全部；OPERATOR 只能访问自己任务下的 Listing。"""
    from app.api.distributions import _task_for_user

    listing = get_listing(db, listing_id)
    task = db.get(DistributionTask, listing.distribution_task_id)
    if task is None:
        raise ListingNotFound()
    # 借用 Distribution 的取权逻辑（OPERATOR 他人任务 → 404 不泄露）
    _task_for_user(db, task.id, current_user)
    return listing


def patch_listing(
    db: Session,
    listing: Listing,
    *,
    listing_url: str | None,
    parent_asin: str | None,
    store: str | None,
    site: str | None,
    actor_name: str,
) -> Listing:
    """补 Parent ASIN（更新本行，不新建）/ 修 URL / 店铺站点。"""
    task = db.get(DistributionTask, listing.distribution_task_id)

    if listing_url is not None:
        url = normalize_listing_url(listing_url)
        if url != listing.listing_url:
            duplicate = db.execute(
                select(Listing.id).where(Listing.listing_url == url, Listing.id != listing.id)
            ).scalar_one_or_none()
            if duplicate is not None:
                raise ValidationError("该链接已存在于另一条 Listing，请直接使用该链接")
            before = listing.listing_url
            listing.listing_url = url
            write_log(
                db,
                design_package_id=task.design_package_id if task else None,
                target_type="LISTING",
                target_id=listing.id,
                actor=actor_name,
                action="LISTING_URL_UPDATED",
                summary="修改 Listing URL",
                before_value=before,
                after_value=url,
            )

    # parent_asin: None=不动；""=清空；有值=校验后写入（区分首次后补 / 更新）
    if parent_asin is not None:
        parent = _validate_parent_asin(parent_asin)
        previous = listing.parent_asin
        if parent != previous:
            listing.parent_asin = parent
            if parent and not previous:
                listing.parent_asin_bound_at = utcnow()
                action, summary = "PARENT_ASIN_BOUND", f"后补 Parent ASIN {parent}"
            elif parent:
                action, summary = "PARENT_ASIN_UPDATED", f"更新 Parent ASIN {previous} → {parent}"
            else:
                action, summary = "PARENT_ASIN_UPDATED", f"清空 Parent ASIN（原 {previous}）"
            write_log(
                db,
                design_package_id=task.design_package_id if task else None,
                target_type="LISTING",
                target_id=listing.id,
                actor=actor_name,
                action=action,
                summary=summary,
                before_value=previous,
                after_value=parent,
            )

    if store is not None:
        listing.store = store.strip() or None
    if site is not None:
        listing.site = site.strip() or None
    listing.updated_at = utcnow()
    db.flush()
    return listing


# ---------------------------------------------------------------- 素材 ↔ Listing 关联


def link_material(
    db: Session,
    listing: Listing,
    *,
    material_id: str | None,
    variant_id: str | None,
    actor: str,
) -> ListingMaterial:
    """建立素材 ↔ Listing 关联（materialId=MAT / variantId=副素材，至少一项；幂等）。"""
    material_id = (material_id or "").strip() or None
    variant_id = (variant_id or "").strip() or None
    if not material_id and not variant_id:
        raise ValidationError("materialId 与 variantId 至少填一项")

    if material_id and db.get(Material, material_id) is None:
        raise NotFoundError("主素材不存在")
    if variant_id and db.get(MaterialVariant, variant_id) is None:
        raise NotFoundError("副素材不存在")

    duplicate = db.execute(
        select(ListingMaterial).where(
            ListingMaterial.listing_id == listing.id,
            ListingMaterial.material_id == material_id,
            ListingMaterial.variant_id == variant_id,
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        return duplicate

    row = ListingMaterial(
        id=new_id("lmat"), listing_id=listing.id, material_id=material_id, variant_id=variant_id
    )
    db.add(row)
    db.flush()
    task = db.get(DistributionTask, listing.distribution_task_id)
    write_log(
        db,
        design_package_id=task.design_package_id if task else None,
        target_type="LISTING",
        target_id=listing.id,
        actor=actor,
        action="LISTING_MATERIAL_LINKED",
        summary=f"关联素材：{material_id or variant_id}",
        after_value=material_id or variant_id,
    )
    return row


def unlink_material(
    db: Session,
    listing: Listing,
    *,
    material_id: str | None,
    variant_id: str | None,
    actor: str,
) -> None:
    material_id = (material_id or "").strip() or None
    variant_id = (variant_id or "").strip() or None
    if not material_id and not variant_id:
        raise ValidationError("materialId 与 variantId 至少填一项")
    row = db.execute(
        select(ListingMaterial).where(
            ListingMaterial.listing_id == listing.id,
            ListingMaterial.material_id == material_id,
            ListingMaterial.variant_id == variant_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFoundError("该素材未关联到此 Listing")
    db.delete(row)
    db.flush()
    task = db.get(DistributionTask, listing.distribution_task_id)
    write_log(
        db,
        design_package_id=task.design_package_id if task else None,
        target_type="LISTING",
        target_id=listing.id,
        actor=actor,
        action="LISTING_MATERIAL_UNLINKED",
        summary=f"解除素材关联：{material_id or variant_id}",
        before_value=material_id or variant_id,
    )


# ---------------------------------------------------------------- 查询


def list_task_listings(db: Session, task_id: str) -> list[Listing]:
    return list(
        db.execute(
            select(Listing)
            .where(Listing.distribution_task_id == task_id)
            .order_by(Listing.created_at.asc())
        ).scalars()
    )


def listings_for_material(db: Session, material_id: str) -> list[Listing]:
    return list(
        db.execute(
            select(Listing)
            .join(ListingMaterial, ListingMaterial.listing_id == Listing.id)
            .where(ListingMaterial.material_id == material_id)
            .order_by(Listing.created_at.desc())
        ).scalars()
    )


def listings_for_variant(db: Session, variant_id: str) -> list[Listing]:
    return list(
        db.execute(
            select(Listing)
            .join(ListingMaterial, ListingMaterial.listing_id == Listing.id)
            .where(ListingMaterial.variant_id == variant_id)
            .order_by(Listing.created_at.desc())
        ).scalars()
    )


def to_listing_dto(db: Session, row: Listing):
    from app.schemas.dto import ListingDTO

    rows = db.execute(
        select(ListingMaterial).where(ListingMaterial.listing_id == row.id)
    ).scalars().all()
    material_count = sum(1 for r in rows if r.material_id)
    variant_count = sum(1 for r in rows if r.variant_id)
    return ListingDTO.from_entity(row, material_count=material_count, variant_count=variant_count)


# ---------------------------------------------------------------- 历史数据同步（与 migration 0013 同口径）


def sync_legacy_parent_listings(db: Session) -> int:
    """把 distribution_parent_asins 存量复制为 listings（幂等）。

    迁移口径与 0013 相同：不覆盖原表、URL 空保留 NULL（不伪造）、
    parent_asin 原值保留、按任务快照自动建立素材关联。测试与运维可重复调用。
    """
    synced = 0
    legacy_rows = list(
        db.execute(
            select(DistributionParentAsin).order_by(DistributionParentAsin.created_at.asc())
        ).scalars()
    )
    for pa in legacy_rows:
        existing = db.get(Listing, f"listing-{pa.id}")
        if existing is not None:
            continue
        task = db.get(DistributionTask, pa.distribution_task_id)
        if task is None:
            continue
        url = (pa.listing_url or "").strip() or None
        # 防重复：同 URL 已有正式 Listing 时不再复制
        if url:
            dup = db.execute(select(Listing).where(Listing.listing_url == url)).scalar_one_or_none()
            if dup is not None:
                continue
        parent = (pa.parent_asin or "").strip() or None
        listing = Listing(
            id=f"listing-{pa.id}",
            distribution_task_id=pa.distribution_task_id,
            listing_url=url,
            parent_asin=parent,
            parent_asin_bound_at=pa.created_at if parent else None,
            site=pa.site,
            operator_user_id=task.operator_user_id,
            created_by=task.operator_name or "系统迁移",
            created_at=pa.created_at,
            updated_at=pa.created_at,
        )
        db.add(listing)
        db.flush()
        link_task_materials(db, listing, task, task.operator_name or "系统迁移")
        synced += 1
    db.flush()
    return synced


__all__ = [
    "ListingNotFound",
    "create_listing",
    "get_listing",
    "get_listing_for_user",
    "link_material",
    "link_task_materials",
    "list_task_listings",
    "listings_for_material",
    "listings_for_variant",
    "normalize_listing_url",
    "patch_listing",
    "sync_legacy_parent_listings",
    "to_listing_dto",
    "unlink_material",
]
