# ============================================================================
# Variant 图片角色（variant_effect_images）服务
#
# variant_effect_images 已能表达 Variant 的图片角色：
#   MATERIAL_SOURCE（素材源图）/ FINAL_EFFECT（最终效果图，Black / White 用
#   sole_color 区分）/ PREVIEW_ONLY。
# 这里只提供「登记」与「读取」：**优先复用已有 Asset**（asset_id），
# 绝不重复创建图片实体；没有 Asset 时可存外部 source_url。
# ============================================================================

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AssetNotFound, VariantNotFound
from app.db.models import Asset, MaterialVariant, VariantEffectImage
from app.schemas.dto import VariantEffectImageDTO
from app.services.activity import write_log
from app.services.repository import new_id

VALID_ROLES = {"MATERIAL_SOURCE", "FINAL_EFFECT", "PREVIEW_ONLY"}


def set_variant_effect_image(
    db: Session,
    variant_id: str,
    *,
    image_role: str,
    sole_color: str | None,
    asset_id: str | None,
    source_url: str | None,
    actor: str,
) -> VariantEffectImage:
    """为 Variant 登记/覆盖一张图片角色。同一 (variant, role, sole_color) 只保留一行。"""
    variant = db.get(MaterialVariant, variant_id)
    if variant is None:
        raise VariantNotFound()

    if image_role not in VALID_ROLES:
        from app.core.errors import ValidationError

        raise ValidationError(f"不支持的图片角色：{image_role}")

    if image_role == "FINAL_EFFECT":
        color = (sole_color or "").upper()
        if color not in ("BLACK", "WHITE"):
            from app.core.errors import ValidationError

            raise ValidationError("FINAL_EFFECT 必须区分 BLACK / WHITE")
        sole_color = color
    else:
        sole_color = sole_color or None

    if not asset_id and not source_url:
        from app.core.errors import ValidationError

        raise ValidationError("assetId 与 sourceUrl 至少填一项")

    if asset_id and db.get(Asset, asset_id) is None:
        raise AssetNotFound()

    row = (
        db.execute(
            select(VariantEffectImage).where(
                VariantEffectImage.variant_id == variant_id,
                VariantEffectImage.image_role == image_role,
                VariantEffectImage.sole_color == sole_color,
            )
        ).scalar_one_or_none()
    )
    if row is None:
        row = VariantEffectImage(
            id=new_id("effectimg"),
            variant_id=variant_id,
            image_role=image_role,
            sole_color=sole_color,
            asset_id=asset_id,
            source_url=source_url,
            created_by=actor,
        )
        db.add(row)
    else:
        row.asset_id = asset_id
        row.source_url = source_url
    db.flush()

    write_log(
        db,
        design_package_id=None,
        target_type="MATERIAL_VARIANT",
        target_id=variant_id,
        actor=actor,
        action="SET_EFFECT_IMAGE",
        summary=(
            f"{variant.display_code} 登记图片角色 {image_role}"
            + (f"/{sole_color}" if sole_color else "")
        ),
        after_value=asset_id or source_url or "",
    )
    return row


def list_variant_effect_images(db: Session, variant_id: str) -> list[VariantEffectImageDTO]:
    rows = list(
        db.execute(
            select(VariantEffectImage)
            .where(VariantEffectImage.variant_id == variant_id)
            .order_by(VariantEffectImage.image_role, VariantEffectImage.sole_color)
        ).scalars()
    )
    return [VariantEffectImageDTO.from_entity(row) for row in rows]


__all__ = ["list_variant_effect_images", "set_variant_effect_image"]
