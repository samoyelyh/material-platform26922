# ============================================================================
# 对 order-center 的只读素材契约 API
#
#   GET /api/contract/materials-by-child-asin/{child_asin}
#     返回：category_code + Distribution/Batch + Variant 候选 + 图片角色
#           （MATERIAL_SOURCE / FINAL_EFFECT Black-White）
#
# 契约原则：只读 / DTO 稳定 / 不暴露内部 ORM / 不让订单中心依赖素材库表结构。
# ============================================================================

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.db.session import get_db
from app.schemas.dto import ContractChildAsinResponseDTO
from app.services.contract_service import material_facts_by_child_asin

router = APIRouter(tags=["contract"])


class ChildAsinNotFound(NotFoundError):
    code = "CHILD_ASIN_NOT_FOUND"
    default_message = "未找到该 Child ASIN 对应的素材事实"


@router.get(
    "/contract/materials-by-child-asin/{child_asin}",
    response_model=ContractChildAsinResponseDTO,
    summary="只读契约：Child ASIN → Batch → Variant 候选 + 图片角色",
    description=(
        "订单中心通过它缩小素材识别范围。返回该 Child ASIN 关联派发任务对应的 "
        "Batch 及其整套 Variant 候选（一个 Batch 可能含整套副素材，不是固定单个），"
        "并附带 MATERIAL_SOURCE / FINAL_EFFECT（Black / White）图片角色。"
        "已取消派发任务不对外提供素材事实。"
    ),
)
def materials_by_child_asin(
    child_asin: str, db: Session = Depends(get_db)
) -> ContractChildAsinResponseDTO:
    result = material_facts_by_child_asin(db, child_asin)
    if result is None:
        raise ChildAsinNotFound(f"未找到 Child ASIN {child_asin} 对应的素材事实")
    return result
