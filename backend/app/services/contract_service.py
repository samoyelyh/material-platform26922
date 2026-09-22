# ============================================================================
# 对 order-center 的只读素材契约
#
# 目的：订单中心只通过稳定、只读的 DTO 获取「Child ASIN → Batch → Variant 候选
# + 图片角色」，不依赖素材库 ORM / 表结构。素材库内部改表时不影响订单中心。
#
# 检索链：child_asin → distribution_child_asins → distribution_tasks（排除已取消）
#        → DerivativeBatch → MaterialVariant 候选 + DesignPackage.category_code
#
# 图片角色（复用已有 Asset / variant_effect_images，绝不新建图片实体）：
#   MATERIAL_SOURCE  = Variant 当前 Revision 的素材源 JPG
#   FINAL_EFFECT     = variant_effect_images.image_role='FINAL_EFFECT'，
#                      用 sole_color 区分 BLACK / WHITE
# ============================================================================

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Asset,
    DesignPackage,
    DerivativeBatch,
    DistributionChildAsin,
    DistributionTask,
    Material,
    MaterialVariant,
    VariantEffectImage,
    VariantRevision,
)
from app.schemas.dto import (
    ContractBatchDTO,
    ContractChildAsinResponseDTO,
    ContractVariantDTO,
    ContractVariantImageDTO,
)
from app.schemas.mappers import asset_content_url


def material_facts_by_child_asin(db: Session, child_asin: str) -> ContractChildAsinResponseDTO | None:
    """
    按 Child ASIN 返回素材事实。找不到返回 None（HTTP 404 由路由层处理）。

    契约口径：
      - 只从「未取消」的派发任务关联里检索（已取消的任务不对外提供素材事实）
      - 返回该 Batch 的整套 Variant 候选（一个 Batch 可能含整套副素材，不是固定单个）
      - 仅返回未删除的 Variant
    """
    child_asin = (child_asin or "").strip().upper()
    if not child_asin:
        return None

    child = (
        db.execute(
            select(DistributionChildAsin)
            .where(DistributionChildAsin.child_asin == child_asin)
            .order_by(DistributionChildAsin.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
    )
    if child is None:
        return None

    task = db.get(DistributionTask, child.distribution_task_id)
    if task is None or task.status == "CANCELLED":
        return None

    batch = db.get(DerivativeBatch, task.batch_id)
    if batch is None:
        return None
    package = db.get(DesignPackage, batch.design_package_id)
    if package is None:
        return None

    variants = _variant_candidates(db, batch, package)
    return ContractChildAsinResponseDTO(
        childAsin=child_asin,
        categoryCode=package.category_code,
        batch=ContractBatchDTO(
            batchId=batch.id,
            batchCode=batch.code,
            versionNo=batch.version_no,
            categoryCode=package.category_code,
            categoryName=package.category_name,
            designPackageId=package.id,
        ),
        variants=variants,
    )


def _variant_candidates(
    db: Session, batch: DerivativeBatch, package: DesignPackage
) -> list[ContractVariantDTO]:
    variants = list(
        db.execute(
            select(MaterialVariant)
            .where(MaterialVariant.batch_id == batch.id, MaterialVariant.deleted.is_(False))
        ).scalars()
    )
    if not variants:
        return []

    material_ids = {v.material_id for v in variants}
    materials = {
        row.id: row
        for row in db.execute(select(Material).where(Material.id.in_(material_ids))).scalars()
    }

    revision_ids = {v.current_revision_id for v in variants if v.current_revision_id}
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

    effect_rows = list(
        db.execute(
            select(VariantEffectImage).where(
                VariantEffectImage.variant_id.in_([v.id for v in variants])
            )
        ).scalars()
    )
    effect_assets = {
        row.asset_id: db.get(Asset, row.asset_id)
        for row in effect_rows
        if row.asset_id and db.get(Asset, row.asset_id) is not None
    }
    effects_by_variant: dict[str, list[VariantEffectImage]] = {}
    for row in effect_rows:
        effects_by_variant.setdefault(row.variant_id, []).append(row)

    dtos: list[ContractVariantDTO] = []
    for variant in variants:
        material = materials.get(variant.material_id)
        images: list[ContractVariantImageDTO] = []

        # MATERIAL_SOURCE = 当前 Revision 的素材源 JPG（已有 Asset，复用）
        revision = revisions.get(variant.current_revision_id or "")
        if revision is not None:
            source_asset = assets.get(revision.asset_id)
            if source_asset is not None:
                images.append(
                    ContractVariantImageDTO(
                        imageRole="MATERIAL_SOURCE",
                        assetId=source_asset.id,
                        uri=asset_content_url(source_asset.id),
                    )
                )

        # FINAL_EFFECT = variant_effect_images 行（sole_color 区分 BLACK / WHITE）
        for effect in effects_by_variant.get(variant.id, []):
            if effect.image_role != "FINAL_EFFECT":
                continue
            eff_asset = effect_assets.get(effect.asset_id or "")
            uri = asset_content_url(eff_asset.id) if eff_asset else effect.source_url
            images.append(
                ContractVariantImageDTO(
                    imageRole=effect.image_role,
                    soleColor=effect.sole_color,
                    assetId=effect.asset_id,
                    uri=uri,
                )
            )

        dtos.append(
            ContractVariantDTO(
                variantId=variant.id,
                displayCode=variant.display_code,
                materialId=variant.material_id,
                materialCode=material.material_code if material else "",
                categoryCode=package.category_code,
                batchId=batch.id,
                images=images,
            )
        )

    dtos.sort(key=lambda dto: (dto.displayCode, dto.variantId))
    return dtos


__all__ = ["material_facts_by_child_asin"]
