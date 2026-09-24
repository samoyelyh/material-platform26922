# ============================================================================
# 对 order-center 的只读素材契约测试
#
# 覆盖：
#   - Child ASIN → Batch → Variant 候选（一整套副素材，不是固定单个）
#   - category_code 来自所属设计包
#   - 图片角色：MATERIAL_SOURCE（当前 Revision 源图）+ FINAL_EFFECT Black/White
#     （variant_effect_images，sole_color 区分）
#   - 已取消派发任务不对外提供素材事实（契约不外泄）
#   - 契约响应不暴露内部 ORM / 存储键
#   - FINAL_EFFECT 必须区分 BLACK / WHITE（服务端校验）
# ============================================================================

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import DerivativeBatch, MaterialVariant, VariantRevision


def _confirm_all(client, upload_id: str) -> dict:
    response = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert response.status_code == 200, response.text
    return response.json()


def _create_v1(client, pkg_id: str, upload_id: str) -> dict:
    response = client.post(
        f"/api/design-packages/{pkg_id}/batches", json={"actor": "设计美工-阿May", "uploadId": upload_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


def _dispatch(client, pkg_id: str, manager_headers: dict, operator_user) -> dict:
    response = client.post(
        f"/api/design-packages/{pkg_id}/distributions",
        json={"operatorUserId": operator_user.id},
        headers=manager_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def _bind_asins(client, task_id: str, parent: str, children: list[str], operator_headers: dict) -> dict:
    response = client.put(
        f"/api/distributions/{task_id}/asins",
        json={"parentAsin": parent, "children": children, "site": "US", "actor": "张三"},
        headers=operator_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_contract_material_by_child_asin_happy_path(client, phase2_package, auth_users, auth_tokens):
    """Child ASIN → Batch → 整套 Variant 候选 + category_code + 图片角色。"""
    ctx = phase2_package(3, tags=["球迷款"], design_code="DS-CONTRACT-001")
    pkg_id = ctx["pkg"]["id"]
    upload_id = ctx["upload_id"]

    _confirm_all(client, upload_id)
    batch = _create_v1(client, pkg_id, upload_id)
    assert batch["code"] == "V1"
    variants = batch["variants"]
    assert len(variants) == 3
    display_codes = [v["displayCode"] for v in variants]
    assert display_codes == ["1-1", "2-1", "3-1"]
    variant_ids = [v["id"] for v in variants]
    # 每个 Variant 的当前 Revision Asset 就是 MATERIAL_SOURCE 源图（复用已有 Asset）
    source_assets = {v["id"]: v["assetId"] for v in variants}
    assert all(source_assets.values())

    # 为每个 Variant 登记 FINAL_EFFECT Black/White（复用已有 Asset，不新建图片实体）
    for vid in variant_ids:
        for color in ("BLACK", "WHITE"):
            r = client.post(
                f"/api/material-variants/{vid}/effect-images",
                json={
                    "imageRole": "FINAL_EFFECT",
                    "soleColor": color,
                    "assetId": source_assets[vid],
                    "actor": "肖芸",
                },
            )
            assert r.status_code == 200, r.text

    task = _dispatch(client, pkg_id, auth_tokens["manager"], auth_users["operator"])
    task_id = task["id"]
    assert task["versionCode"] == "V1"
    assert task["variantCount"] == 3
    assert task["status"] == "ACTIVE"

    child_asin = "B0ABCDEFGH"
    _bind_asins(client, task_id, "B0ZZZZZZZZ", [child_asin, "B0AAAAAAAB"], auth_tokens["operator"])

    # ---- 契约查询 ----
    response = client.get(f"/api/contract/materials-by-child-asin/{child_asin}")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["childAsin"] == child_asin
    pkg_dto = client.get(f"/api/design-packages/{pkg_id}").json()
    assert body["categoryCode"] == pkg_dto["categoryCode"]
    assert body["batch"]["batchId"] == batch["id"]
    assert body["batch"]["batchCode"] == "V1"
    assert body["batch"]["versionNo"] == 1
    assert body["batch"]["designPackageId"] == pkg_id

    # 一整套 Variant 候选（不是固定单个）
    assert len(body["variants"]) == 3
    by_code = {v["displayCode"]: v for v in body["variants"]}
    assert set(by_code) == {"1-1", "2-1", "3-1"}

    v1 = by_code["1-1"]
    assert v1["variantId"] == variant_ids[0]
    assert v1["batchId"] == batch["id"]
    assert v1["materialId"]
    assert v1["materialCode"].startswith("MAT-")
    assert v1["categoryCode"] == body["categoryCode"]

    # 图片角色：MATERIAL_SOURCE + FINAL_EFFECT Black/White 可区分
    roles = {img["imageRole"] + "/" + (img["soleColor"] or "NONE"): img for img in v1["images"]}
    assert "MATERIAL_SOURCE/NONE" in roles
    assert "FINAL_EFFECT/BLACK" in roles
    assert "FINAL_EFFECT/WHITE" in roles
    assert roles["MATERIAL_SOURCE/NONE"]["assetId"] == source_assets[variant_ids[0]]
    assert roles["FINAL_EFFECT/BLACK"]["soleColor"] == "BLACK"
    assert roles["FINAL_EFFECT/WHITE"]["soleColor"] == "WHITE"
    # uri 是后端代理地址，不是内部 storage_key
    assert all(img["uri"] for img in v1["images"])
    for img in v1["images"]:
        assert img["uri"].startswith("/api/assets/")
        assert "storage_key" not in str(img)
        assert "blake3" not in str(img)

    # 契约响应不暴露内部 ORM / 存储字段
    raw = response.text
    for leaked in ("storageKey", "storage_key", "blake3", "phash", "created_by", "createdBy"):
        assert leaked not in raw, f"契约泄漏内部字段：{leaked}"


def test_contract_other_child_asin_shares_same_batch(client, phase2_package, auth_users, auth_tokens):
    """Parent 下多个 Child 默认共享同一套 Batch / Variant（不是 Child → 单张副素材）。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    _bind_asins(client, task["id"], "B0ZZZZZZZZ", ["B0AAAAAAAB", "B0CCCCCCCD"], auth_tokens["operator"])

    a = client.get("/api/contract/materials-by-child-asin/B0AAAAAAAB").json()
    b = client.get("/api/contract/materials-by-child-asin/B0CCCCCCCD").json()
    assert a["batch"]["batchId"] == b["batch"]["batchId"]
    assert [v["variantId"] for v in a["variants"]] == [v["variantId"] for v in b["variants"]]


def test_contract_child_asin_not_found(client, phase2_package, auth_users, auth_tokens):
    """未绑定的 Child ASIN → 404，不返回编造关系。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    # 未绑定任何 asin
    response = client.get("/api/contract/materials-by-child-asin/B0NOPE0001")
    assert response.status_code == 404
    assert response.json()["code"] == "CHILD_ASIN_NOT_FOUND"


def test_contract_cancelled_distribution_is_not_exposed(client, phase2_package, auth_users, auth_tokens):
    """已取消的派发任务不对外提供素材事实。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    _bind_asins(client, task["id"], "B0ZZZZZZZZ", ["B0AAAAAAAB"], auth_tokens["operator"])
    # 取消派发（管理角色）
    cancel = client.post(f"/api/distributions/{task['id']}/cancel", headers=auth_tokens["manager"])
    assert cancel.status_code == 200, cancel.text
    assert cancel.json()["status"] == "CANCELLED"

    response = client.get("/api/contract/materials-by-child-asin/B0AAAAAAAB")
    assert response.status_code == 404


def test_contract_task_overview_and_receive(client, phase2_package, auth_users, auth_tokens):
    """派发 → 接收 → 回填 ASIN 的真实后端链路（此前仅前端 Mock）。"""
    ctx = phase2_package(3)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])

    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    task_id = task["id"]
    assert task["packageName"] == ctx["pkg"]["name"]
    assert task["packageCode"] == ctx["pkg"]["code"]
    assert task["variantCount"] == 3

    received = client.post(f"/api/distributions/{task_id}/receive", headers=auth_tokens["operator"])
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "RECEIVED"
    assert received.json()["receivedAt"]

    detail = client.get(f"/api/distributions/{task_id}", headers=auth_tokens["operator"])
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["batchId"] == batch["id"]
    assert len(body["items"]) == 3
    assert body["deliveryRound"] == 1
    # 快照：item 固定了 variant + revision
    assert all(it["variantId"] for it in body["items"])
    assert all(it["revisionId"] for it in body["items"])


def test_final_effect_requires_black_or_white(client, phase2_package):
    """FINAL_EFFECT 必须区分 BLACK / WHITE，否则 422。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]
    asset_id = batch["variants"][0]["assetId"]

    r = client.post(
        f"/api/material-variants/{vid}/effect-images",
        json={"imageRole": "FINAL_EFFECT", "assetId": asset_id, "actor": "肖芸"},
    )
    assert r.status_code == 422
    assert "BLACK / WHITE" in r.json()["message"]

    # 有颜色则成功
    ok = client.post(
        f"/api/material-variants/{vid}/effect-images",
        json={"imageRole": "FINAL_EFFECT", "soleColor": "BLACK", "assetId": asset_id, "actor": "肖芸"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["soleColor"] == "BLACK"
