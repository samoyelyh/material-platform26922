# ============================================================================
# 上架 Listing API（素材 ↔ 链接 多对多）
#
#   POST   /api/distributions/{task_id}/listings        新增上架链接（URL 必填，Parent ASIN 可空）
#                                                        同 URL 已存在 → 返回现有并自动关联任务素材
#   GET    /api/distributions/{task_id}/listings        该任务的全部上架链接
#   GET    /api/listings/{listing_id}                   单条 Listing 详情
#   PATCH  /api/listings/{listing_id}                  补 Parent ASIN / 改 URL / 店铺站点
#   POST   /api/listings/{listing_id}/materials         建立素材 ↔ Listing 关联（MAT / Variant）
#   DELETE /api/listings/{listing_id}/materials         解除素材 ↔ Listing 关联
#   GET    /api/materials/{material_code}/listings     主素材（MAT）被哪些 Listing 使用
#   GET    /api/material-variants/{variant_id}/listings 副素材出现在哪些 Listing
#
# 权限沿用 Distribution：OPERATOR 只能操作自己任务下的 Listing；DESIGNER 无运营任务访问权（404）；
# ADMIN / DESIGN_MANAGER 可查看与操作全部。不要求 Child ASIN（它不再参与素材归属）。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.distributions import _actor, _task_for_user
from app.core.auth import get_current_user
from app.core.errors import MaterialNotFound
from app.db.models import DistributionTask, Material, User
from app.db.session import get_db
from app.schemas.dto import (
    ListingCreateRequest,
    ListingDTO,
    ListingMaterialLinkRequest,
    ListingPatchRequest,
)
from app.services.listing_service import (
    create_listing,
    get_listing,
    get_listing_for_user,
    link_material,
    list_task_listings,
    listings_for_material,
    listings_for_variant,
    patch_listing,
    to_listing_dto,
    unlink_material,
)

router = APIRouter(tags=["listings"])


class ListingCreateResponse(BaseModel):
    listing: ListingDTO
    existed: bool = Field(default=False, description="true=该 URL 已存在，返回的是现有 Listing（已自动关联当前任务素材）")


@router.post(
    "/distributions/{task_id}/listings",
    response_model=ListingCreateResponse,
    summary="新增上架链接（URL 必填，Parent ASIN 可空后补；同 URL 重复时关联现有 Listing）",
)
def create_task_listing(
    task_id: str,
    payload: ListingCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ListingCreateResponse:
    task = _task_for_user(db, task_id, current_user)
    listing, created = create_listing(
        db,
        task,
        listing_url=payload.listingUrl,
        parent_asin=payload.parentAsin,
        store=payload.store,
        site=payload.site,
        actor_name=_actor(payload.actor, current_user),
        operator_user_id=current_user.id,
    )
    db.commit()
    return ListingCreateResponse(listing=to_listing_dto(db, listing), existed=not created)


@router.get(
    "/distributions/{task_id}/listings",
    response_model=list[ListingDTO],
    summary="该任务的上架链接列表（Parent ASIN 为空 = 待补充）",
)
def task_listings(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ListingDTO]:
    _task_for_user(db, task_id, current_user)
    return [to_listing_dto(db, row) for row in list_task_listings(db, task_id)]


@router.get(
    "/listings/{listing_id}",
    response_model=ListingDTO,
    summary="单条 Listing 详情",
)
def listing_detail(
    listing_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ListingDTO:
    listing = get_listing_for_user(db, listing_id, current_user)
    return to_listing_dto(db, listing)


@router.patch(
    "/listings/{listing_id}",
    response_model=ListingDTO,
    summary="补 Parent ASIN（更新本行不新建）/ 修改 URL / 店铺站点",
)
def patch_listing_endpoint(
    listing_id: str,
    payload: ListingPatchRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ListingDTO:
    listing = get_listing_for_user(db, listing_id, current_user)
    listing = patch_listing(
        db,
        listing,
        listing_url=payload.listingUrl,
        parent_asin=payload.parentAsin,
        store=payload.store,
        site=payload.site,
        actor_name=_actor(payload.actor, current_user),
    )
    db.commit()
    return to_listing_dto(db, listing)


@router.post(
    "/listings/{listing_id}/materials",
    response_model=ListingDTO,
    summary="建立素材 ↔ Listing 关联（materialId=MAT / variantId=副素材，至少一项）",
)
def link_listing_material(
    listing_id: str,
    payload: ListingMaterialLinkRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ListingDTO:
    listing = get_listing_for_user(db, listing_id, current_user)
    link_material(
        db,
        listing,
        material_id=payload.materialId,
        variant_id=payload.variantId,
        actor=_actor(payload.actor, current_user),
    )
    db.commit()
    return to_listing_dto(db, listing)


@router.delete(
    "/listings/{listing_id}/materials",
    response_model=ListingDTO,
    summary="解除素材 ↔ Listing 关联",
)
def unlink_listing_material(
    listing_id: str,
    materialId: str | None = Query(default=None, description="主素材 MAT id"),  # noqa: N803
    variantId: str | None = Query(default=None, description="副素材 id"),  # noqa: N803
    actor: str | None = Query(default=None, max_length=128),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ListingDTO:
    listing = get_listing_for_user(db, listing_id, current_user)
    unlink_material(db, listing, material_id=materialId, variant_id=variantId, actor=_actor(actor, current_user))
    db.commit()
    return to_listing_dto(db, listing)


# ---------------------------------------------------------------- 素材维度（素材详情「上架信息」用）


@router.get(
    "/materials/{material_code}/listings",
    response_model=list[ListingDTO],
    summary="主素材（MAT）被哪些 Listing 使用",
)
def material_listing_listings(
    material_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ListingDTO]:
    material = db.execute(
        select(Material).where(Material.material_code == material_code)
    ).scalar_one_or_none()
    if material is None:
        raise MaterialNotFound()
    return [to_listing_dto(db, row) for row in listings_for_material(db, material.id)]


@router.get(
    "/material-variants/{variant_id}/listings",
    response_model=list[ListingDTO],
    summary="副素材出现在哪些 Listing",
)
def variant_listing_listings(
    variant_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
) -> list[ListingDTO]:
    return [to_listing_dto(db, row) for row in listings_for_variant(db, variant_id)]
