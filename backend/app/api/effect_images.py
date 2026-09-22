# ============================================================================
# Variant 效果图角色 API
#
#   POST /api/material-variants/{id}/effect-images/upload   上传新效果图 → 创建/复用
#                                                          Asset → 关联到 Variant
#   POST /api/material-variants/{id}/effect-images          复用已有 Asset 登记/覆盖角色
#   GET  /api/material-variants/{id}/effect-images          该 Variant 的全部图片角色
#
# 复用原则（P1）：效果图文件走与素材一致的 Asset / 对象存储 / BLAKE3 去重，
# **不重新建立文件上传体系**：
#   - 上传新图 → compute_blake3/phash → 命中相同 BLAKE3 复用已有 Asset，否则写存储建 Asset
#   - 图片 Asset 已存在 → 直接按 assetId 关联，不重复创建物理文件
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    AssetNotFound,
    EmptyFileError,
    FileTooLarge,
    HashComputeError,
    StorageWriteError,
    UnsupportedFileType,
    ValidationError,
)
from app.db.models import Asset, MaterialVariant, VariantEffectImage
from app.db.session import get_db
from app.schemas.dto import VariantEffectImageCreateRequest, VariantEffectImageDTO
from app.services.activity import write_log
from app.services.effect_image_service import (
    VALID_ROLES,
    list_variant_effect_images,
    set_variant_effect_image,
)
from app.services.fingerprint import (
    PHASH_VERSION,
    compute_blake3,
    compute_phash,
    extract_image_metadata,
    is_image_mime,
)
from app.services.repository import find_asset_by_blake3, new_id
from app.services.storage import build_storage_key, get_storage

router = APIRouter(tags=["material-variants"])

DEFAULT_ACTOR = "肖芸"


def _classify_image(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext in settings.allowed_image_ext:
        return "image/jpeg" if ext in {"jpg", "jpeg"} else ("image/png" if ext == "png" else "application/octet-stream")
    raise UnsupportedFileType(
        f"效果图仅支持图片格式：{filename}（支持 {', '.join(settings.allowed_image_ext)}）"
    )


@router.post(
    "/material-variants/{variant_id}/effect-images/upload",
    response_model=VariantEffectImageDTO,
    summary="上传新效果图：创建/复用 Asset → 关联到 Variant（MATERIAL_SOURCE / FINAL_EFFECT Black-White）",
)
async def upload_effect_image(
    variant_id: str,
    file: UploadFile = File(..., description="图片文件（效果图）"),
    imageRole: str = Form(..., description="MATERIAL_SOURCE / FINAL_EFFECT / PREVIEW_ONLY"),  # noqa: N803
    soleColor: str | None = Form(default=None, description="FINAL_EFFECT 必须为 BLACK / WHITE"),
    actor: str | None = Form(default=None, max_length=128),
    db: Session = Depends(get_db),
) -> VariantEffectImageDTO:
    variant = db.get(MaterialVariant, variant_id)
    if variant is None:
        from app.core.errors import VariantNotFound

        raise VariantNotFound()

    if imageRole not in VALID_ROLES:
        raise ValidationError(f"不支持的图片角色：{imageRole}")

    # FINAL_EFFECT 必须区分 BLACK / WHITE（其余角色不强制）
    color: str | None = None
    if imageRole == "FINAL_EFFECT":
        color = (soleColor or "").upper()
        if color not in ("BLACK", "WHITE"):
            raise ValidationError("FINAL_EFFECT 必须区分 BLACK / WHITE")
        soleColor = color

    filename = file.filename or "unnamed"
    mime_type = _classify_image(filename)

    try:
        data = await file.read()
    except OSError as exc:
        raise StorageWriteError(f"读取上传内容失败：{exc}") from exc
    if not data:
        raise EmptyFileError(f"文件内容为空：{filename}")
    if len(data) > settings.max_upload_size_bytes:
        raise FileTooLarge(
            f"文件超过大小限制（{len(data)} > {settings.max_upload_size_bytes} 字节）：{filename}"
        )

    try:
        blake3_hex = compute_blake3(data)
    except Exception as exc:  # noqa: BLE001
        raise HashComputeError(f"BLAKE3 计算失败：{filename}（{exc}）") from exc

    storage = get_storage()
    existing = find_asset_by_blake3(db, blake3_hex)
    actor_name = (actor or "").strip() or DEFAULT_ACTOR

    if existing is not None:
        # 完全相同文件：复用已有 Asset，不新建物理文件
        asset = existing
    else:
        storage_key = build_storage_key(filename)
        try:
            storage.put(storage_key, data, mime_type)
        except Exception as exc:  # noqa: BLE001
            raise StorageWriteError(f"效果图写入对象存储失败：{filename}（{exc}）") from exc

        phash_bytes = None
        width = height = None
        if is_image_mime(mime_type):
            try:
                phash_bytes = compute_phash(data)
            except Exception:  # noqa: BLE001
                phash_bytes = None
            try:
                width, height = extract_image_metadata(data)
            except Exception:  # noqa: BLE001
                width = height = None

        asset = Asset(
            id=new_id("asset"),
            storage_key=storage_key,
            original_filename=filename,
            mime_type=mime_type,
            size_bytes=len(data),
            width=width,
            height=height,
            blake3=blake3_hex if phash_bytes is not None else None,
            phash=phash_bytes,
            phash_version=PHASH_VERSION if phash_bytes is not None else None,
            created_by=actor_name,
        )
        db.add(asset)
        db.flush()

    row = set_variant_effect_image(
        db,
        variant_id,
        image_role=imageRole,
        sole_color=soleColor,
        asset_id=asset.id,
        source_url=None,
        actor=actor_name,
    )
    db.commit()
    db.refresh(row)
    return VariantEffectImageDTO.from_entity(row)


@router.post(
    "/material-variants/{variant_id}/effect-images",
    response_model=VariantEffectImageDTO,
    summary="为 Variant 登记/覆盖一张图片角色（复用已有 Asset：assetId 或 sourceUrl 二选一）",
)
def add_effect_image(
    variant_id: str,
    payload: VariantEffectImageCreateRequest,
    db: Session = Depends(get_db),
) -> VariantEffectImageDTO:
    actor = (payload.actor or "").strip() or DEFAULT_ACTOR
    row = set_variant_effect_image(
        db,
        variant_id,
        image_role=payload.imageRole,
        sole_color=payload.soleColor,
        asset_id=payload.assetId,
        source_url=payload.sourceUrl,
        actor=actor,
    )
    db.commit()
    return VariantEffectImageDTO.from_entity(row)


@router.get(
    "/material-variants/{variant_id}/effect-images",
    response_model=list[VariantEffectImageDTO],
    summary="该 Variant 的全部图片角色",
)
def get_effect_images(variant_id: str, db: Session = Depends(get_db)) -> list[VariantEffectImageDTO]:
    return list_variant_effect_images(db, variant_id)
