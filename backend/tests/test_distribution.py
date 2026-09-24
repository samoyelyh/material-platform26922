# ============================================================================
# 派发运营 + VariantEffectImage 真实 API 测试
#
# 覆盖：
#   - 派发 → 接收 → 取消 → 回填 ASIN → 列表/详情（真实后端链路）
#   - 同一 (batch, operator) 重复派发 → 拒绝
#   - VariantEffectImage 上传新图 → 新建 Asset；相同 BLAKE3 → 复用同一 Asset
#   - FINAL_EFFECT 非法 sole_color → 422；合法 BLACK/WHITE → 200
#   - 复用已有 Asset 登记角色；不存在的 Variant / Asset → 404
# ============================================================================

from __future__ import annotations

from sqlalchemy import select

from app.db.models import DistributionTask, VariantEffectImage


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


def _upload_effect_image(client, variant_id: str, content: bytes, role: str, sole_color: str | None = None):
    data = {"imageRole": role}
    if sole_color:
        data["soleColor"] = sole_color
    return client.post(
        f"/api/material-variants/{variant_id}/effect-images/upload",
        data=data,
        files={"file": ("effect.png", content, "image/png")},
    )


# ================================================================ 派发 / ASIN


def test_dispatch_receive_cancel_bind_full_flow(client, phase2_package, db_session, auth_users, auth_tokens):
    """派发 → 接收 → 回填 ASIN → 取消 的真实后端链路。"""
    ctx = phase2_package(3)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])

    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    task_id = task["id"]
    assert task["status"] == "ACTIVE"
    assert task["versionCode"] == "V1"
    assert task["variantCount"] == 3
    assert task["batchId"] == batch["id"]

    # 列表
    lst = client.get(f"/api/design-packages/{ctx['pkg']['id']}/distributions", headers=auth_tokens["manager"])
    assert lst.status_code == 200, lst.text
    assert len(lst.json()) == 1

    # 接收（运营本人）
    received = client.post(f"/api/distributions/{task_id}/receive", headers=auth_tokens["operator"])
    assert received.status_code == 200, received.text
    assert received.json()["status"] == "RECEIVED"
    assert received.json()["receivedAt"]

    # 回填 ASIN（运营本人）
    bound = client.put(
        f"/api/distributions/{task_id}/asins",
        json={"parentAsin": "B0PARENT01", "children": ["B0CHILD000", "B0CHILD111"], "site": "US", "actor": "张三"},
        headers=auth_tokens["operator"],
    )
    assert bound.status_code == 200, bound.text
    body = bound.json()
    assert body["status"] == "COMPLETED"
    assert body["parentAsin"]["asin"] == "B0PARENT01"
    assert {c["asin"] for c in body["children"]} == {"B0CHILD000", "B0CHILD111"}

    # 契约能查到
    contract = client.get("/api/contract/materials-by-child-asin/B0CHILD000")
    assert contract.status_code == 200, contract.text
    assert contract.json()["batch"]["batchId"] == batch["id"]

    # 取消（管理角色）
    cancelled = client.post(f"/api/distributions/{task_id}/cancel", headers=auth_tokens["manager"])
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    # 取消后契约不再提供事实
    gone = client.get("/api/contract/materials-by-child-asin/B0CHILD000")
    assert gone.status_code == 404

    # 落库断言
    db_session.rollback()
    tasks = db_session.execute(select(DistributionTask)).scalars().all()
    assert len(tasks) == 1
    assert tasks[0].status == "CANCELLED"
    assert len(tasks[0].items) == 3
    assert len(tasks[0].children) == 2
    assert tasks[0].parent is not None and tasks[0].parent.parent_asin == "B0PARENT01"


def test_dispatch_duplicate_active_rejected(client, phase2_package, auth_users, auth_tokens):
    """同一 (batch, operator) 已存在进行中任务 → 拒绝重复派发。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])

    _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    dup = client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/distributions",
        json={"operatorUserId": auth_users["operator"].id},
        headers=auth_tokens["manager"],
    )
    assert dup.status_code == 422
    assert dup.json()["code"] == "DISTRIBUTION_ALREADY_ACTIVE"

    # 取消后可重新派发给同一运营
    task = client.get(f"/api/design-packages/{ctx['pkg']['id']}/distributions", headers=auth_tokens["manager"]).json()[0]
    cancel = client.post(f"/api/distributions/{task['id']}/cancel", headers=auth_tokens["manager"])
    assert cancel.status_code == 200
    again = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    assert again["id"] != task["id"]


def test_dispatch_requires_batch(client, auth_users, auth_tokens):
    """没有上架版本的设计包不能派发。"""
    response = client.post(
        "/api/design-packages/nonexistent/distributions",
        json={"operatorUserId": auth_users["operator"].id},
        headers=auth_tokens["manager"],
    )
    assert response.status_code == 404


def test_bind_asins_validation(client, phase2_package, auth_users, auth_tokens):
    """回填 ASIN 的格式校验：非法 Parent / 空 Child → 422。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])

    bad_parent = client.put(
        f"/api/distributions/{task['id']}/asins",
        json={"parentAsin": "BAD", "children": ["B0CHILD000"]},
        headers=auth_tokens["operator"],
    )
    assert bad_parent.status_code == 422

    empty_child = client.put(
        f"/api/distributions/{task['id']}/asins",
        json={"parentAsin": "B0PARENT01", "children": []},
        headers=auth_tokens["operator"],
    )
    assert empty_child.status_code == 422


# ================================================================ VariantEffectImage


def _png_bytes(color: tuple[int, int, int], size: int = 32) -> bytes:
    import struct
    import zlib

    def chunk(tag_, data):
        c = tag_ + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    raw = b""
    for y in range(size):
        raw += b"\x00"
        for x in range(size):
            raw += bytes(((color[0] + x) % 256, (color[1] + y) % 256, (color[2] + x + y) % 256))
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def test_effect_image_upload_creates_asset_then_reuses_by_blake3(client, phase2_package, db_session):
    """上传新效果图 → 新建 Asset；相同 BLAKE3 再传 → 复用同一 Asset（不重复物理文件）。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]

    black_png = _png_bytes((10, 10, 10))
    white_png = _png_bytes((240, 240, 240))

    # 1) FINAL_EFFECT/BLACK：上传新图 → 新建 Asset
    r = _upload_effect_image(client, vid, black_png, "FINAL_EFFECT", "BLACK")
    assert r.status_code == 200, r.text
    asset1 = r.json()["assetId"]
    assert asset1

    # 2) FINAL_EFFECT/WHITE：相同字节（BLAKE3 相同）→ 复用同一 Asset，不新建物理文件
    r2 = _upload_effect_image(client, vid, black_png, "FINAL_EFFECT", "WHITE")
    assert r2.status_code == 200, r2.text
    assert r2.json()["assetId"] == asset1

    # 3) 覆盖 BLACK：不同字节 → 新建 Asset，BLACK 行改指新图
    r3 = _upload_effect_image(client, vid, white_png, "FINAL_EFFECT", "BLACK")
    assert r3.status_code == 200, r3.text
    new_asset = r3.json()["assetId"]
    assert new_asset != asset1

    # 落库：角色按 (role, sole_color) 覆盖，共 2 行（BLACK + WHITE）
    db_session.rollback()
    effects = db_session.execute(
        select(VariantEffectImage).where(VariantEffectImage.variant_id == vid)
    ).scalars().all()
    assert len(effects) == 2
    by_color = {e.sole_color: e.asset_id for e in effects}
    assert by_color["BLACK"] == new_asset  # 被第 3 步覆盖为新图
    assert by_color["WHITE"] == asset1  # 第 2 步复用了 BLACK 首次上传的物理文件
    # 两个角色共用一个物理文件（BLAKE3 复用），不重复创建
    assert len(set(by_color.values())) == 2


def test_effect_image_final_effect_requires_black_white(client, phase2_package):
    """FINAL_EFFECT 非法 sole_color → 422。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]

    r = _upload_effect_image(client, vid, _png_bytes((1, 2, 3)), "FINAL_EFFECT", "RED")
    assert r.status_code == 422
    assert "BLACK / WHITE" in r.json()["message"]


def test_effect_image_register_reuse_existing_asset(client, phase2_package):
    """图片 Asset 已存在 → 直接按 assetId 关联，不新建物理文件。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]
    # 复用该 Variant 当前 Revision 的源图 Asset 作为 MATERIAL_SOURCE
    source_asset = batch["variants"][0]["assetId"]
    r = client.post(
        f"/api/material-variants/{vid}/effect-images",
        json={"imageRole": "MATERIAL_SOURCE", "assetId": source_asset, "actor": "肖芸"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["assetId"] == source_asset

    # 查询
    lst = client.get(f"/api/material-variants/{vid}/effect-images")
    assert lst.status_code == 200
    assert any(i["imageRole"] == "MATERIAL_SOURCE" for i in lst.json())


def test_effect_image_variant_and_asset_not_found(client, phase2_package):
    """不存在的 Variant / Asset → 404。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]

    not_found_variant = client.post(
        "/api/material-variants/nonexistent/effect-images",
        json={"imageRole": "MATERIAL_SOURCE", "assetId": batch["variants"][0]["assetId"]},
    )
    assert not_found_variant.status_code == 404

    not_found_asset = client.post(
        f"/api/material-variants/{vid}/effect-images",
        json={"imageRole": "MATERIAL_SOURCE", "assetId": "no-such-asset"},
    )
    assert not_found_asset.status_code == 404


# ================================================================ 新端点：全局列表 / ZIP 下载 / Variant 关联 ASIN


def test_global_distributions_list_and_status_filter(client, phase2_package, auth_users, auth_tokens):
    """GET /api/distributions 全局列表 + 状态筛选。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])

    all_tasks = client.get("/api/distributions", headers=auth_tokens["manager"])
    assert all_tasks.status_code == 200, all_tasks.text
    assert len(all_tasks.json()) == 1
    assert all_tasks.json()[0]["id"] == task["id"]

    active = client.get("/api/distributions?status=ACTIVE", headers=auth_tokens["manager"])
    assert len(active.json()) == 1
    received = client.get("/api/distributions?status=RECEIVED", headers=auth_tokens["manager"])
    assert received.json() == []

    # 接收后状态变化反映到筛选
    client.post(f"/api/distributions/{task['id']}/receive", headers=auth_tokens["operator"])
    assert client.get("/api/distributions?status=RECEIVED", headers=auth_tokens["manager"]).json()[0]["status"] == "RECEIVED"
    assert client.get("/api/distributions?status=ACTIVE", headers=auth_tokens["manager"]).json() == []


def test_download_task_zip_uses_snapshot(client, phase2_package, db_session, auth_users, auth_tokens):
    """下载素材包按派发快照打包；含全部副素材；文件名含设计包/版本/运营/任务。"""
    ctx = phase2_package(3)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])

    resp = client.get(f"/api/distributions/{task['id']}/download", headers=auth_tokens["operator"])
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    disposition = resp.headers.get("content-disposition", "")
    assert "attachment" in disposition and ".zip" in disposition

    # ZIP 内容可解包，条目 = 快照副素材数
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
    assert len(names) == len(batch["variants"])
    assert all(name.split(".")[0] in {"1-1", "2-1", "3-1"} for name in names)


def test_variant_detail_includes_distribution_asins(client, phase2_package, auth_users, auth_tokens):
    """副素材详情反向聚合所属 Batch 的 DistributionTask → Parent/Child ASIN。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]

    # 未派发前：distributions 为空
    before = client.get(f"/api/material-variants/{vid}")
    assert before.status_code == 200
    assert before.json()["distributions"] == []

    # 派发 + 回填 ASIN
    task = _dispatch(client, ctx["pkg"]["id"], auth_tokens["manager"], auth_users["operator"])
    client.put(
        f"/api/distributions/{task['id']}/asins",
        json={"parentAsin": "B0PARENT01", "children": ["B0CHILD000"], "actor": "张三"},
        headers=auth_tokens["operator"],
    )

    after = client.get(f"/api/material-variants/{vid}")
    assert after.status_code == 200
    dists = after.json()["distributions"]
    assert len(dists) == 1
    assert dists[0]["distributionTaskId"] == task["id"]
    assert dists[0]["parentAsin"] == "B0PARENT01"
    assert [c["asin"] for c in dists[0]["children"]] == ["B0CHILD000"]
    assert dists[0]["batchId"] == batch["id"]

    # 取消后不再展示该任务
    client.post(f"/api/distributions/{task['id']}/cancel", headers=auth_tokens["manager"])
    cancelled = client.get(f"/api/material-variants/{vid}")
    assert cancelled.json()["distributions"] == []


def test_effect_image_replace_does_not_delete_reused_asset(client, phase2_package):
    """替换某角色的图时，不误删仍被其它角色复用的 Asset（物理文件不删除）。"""
    ctx = phase2_package(2)
    _confirm_all(client, ctx["upload_id"])
    batch = _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    vid = batch["variants"][0]["id"]

    black_png = _png_bytes((10, 10, 10))
    # BLACK 上传 black.png → Asset A
    r = _upload_effect_image(client, vid, black_png, "FINAL_EFFECT", "BLACK")
    asset_a = r.json()["assetId"]
    # WHITE 复用同一字节 → 同一 Asset A（BLAKE3 复用）
    r2 = _upload_effect_image(client, vid, black_png, "FINAL_EFFECT", "WHITE")
    assert r2.json()["assetId"] == asset_a

    # 覆盖 BLACK 为新图 → Asset B；WHITE 仍指向 A
    r3 = _upload_effect_image(client, vid, _png_bytes((240, 240, 240)), "FINAL_EFFECT", "BLACK")
    asset_b = r3.json()["assetId"]
    assert asset_b != asset_a

    # Asset A 未被删除（仍可读，因为 WHITE 还在用它）
    ga = client.get(f"/api/assets/{asset_a}")
    assert ga.status_code == 200, ga.text
    gc = client.get(f"/api/assets/{asset_a}/content")
    assert gc.status_code == 200, "被 WHITE 复用的 Asset 不应被删除"

    # effect-images 查询：BLACK=B（新）, WHITE=A（复用）
    lst = client.get(f"/api/material-variants/{vid}/effect-images").json()
    by_color = {i["soleColor"]: i["assetId"] for i in lst if i["imageRole"] == "FINAL_EFFECT"}
    assert by_color["BLACK"] == asset_b
    assert by_color["WHITE"] == asset_a

