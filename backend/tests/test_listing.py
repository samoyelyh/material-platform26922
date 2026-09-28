# -*- coding: utf-8 -*-
# ============================================================================
# 上架 Listing 测试（素材 ↔ 链接 多对多；Parent ASIN 后补；Child ASIN 不参与归属）
# ============================================================================

from __future__ import annotations

from sqlalchemy import select, func

from app.db.models import (
    DistributionChildAsin,
    DistributionTask,
    Listing,
    ListingMaterial,
)


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _create_v1(client, pkg_id: str, upload_id: str) -> dict:
    r = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})
    assert r.status_code == 200, r.text
    return r.json()


def _dispatched_task(client, phase2_package, auth_users, auth_tokens, variant_count=2):
    """建包 → 建版 → 派发给 op_a。返回 (task_json, ctx)。"""
    ctx = phase2_package(variant_count)
    _confirm_all(client, ctx["upload_id"])
    _create_v1(client, ctx["pkg"]["id"], ctx["upload_id"])
    r = client.post(
        f"/api/design-packages/{ctx['pkg']['id']}/distributions",
        json={"operatorUserId": auth_users["operator"].id},
        headers=auth_tokens["manager"],
    )
    assert r.status_code == 200, r.text
    return r.json(), ctx


URL_A = "https://www.amazon.com/dp/B0TEST0001?ref=dp_v1_sb_ti&th=1&psc=1&qid=abc"
URL_A_NORMALIZED = "https://www.amazon.com/dp/B0TEST0001"


# ================================================================ 基础：URL 先行 / Parent 后补


def test_listing_url_only_no_parent(client, phase2_package, auth_users, auth_tokens, db_session):
    """1/2：Listing 只填 URL；Parent ASIN 可为空。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    r = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["existed"] is False
    assert body["listing"]["parentAsin"] is None
    # URL 标准化：追踪参数被去掉
    assert body["listing"]["listingUrl"] == URL_A_NORMALIZED
    assert body["listing"]["parentAsinBoundAt"] is None
    # 产生上架记录 → 任务标记上架完成
    assert client.get(f"/api/distributions/{task['id']}", headers=auth_tokens["operator"]).json()["status"] == "COMPLETED"


def test_bind_parent_asin_later_no_new_listing(client, phase2_package, auth_users, auth_tokens, db_session):
    """3/4：后续补 Parent ASIN；补不新建 Listing。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    created = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    ).json()["listing"]
    assert created["parentAsin"] is None

    r = client.patch(
        f"/api/listings/{created['id']}",
        json={"parentAsin": "B0PARENT01"},
        headers=auth_tokens["operator"],
    )
    assert r.status_code == 200, r.text
    patched = r.json()
    assert patched["parentAsin"] == "B0PARENT01"
    assert patched["parentAsinBoundAt"]
    assert patched["id"] == created["id"], "补 Parent ASIN 必须更新原行，不新建"
    assert patched["listingUrl"] == URL_A_NORMALIZED

    db_session.rollback()
    assert db_session.execute(select(func.count(Listing.id))).scalar_one() == 1


def test_invalid_parent_asin_rejected(client, phase2_package, auth_users, auth_tokens):
    """Parent ASIN 格式校验仍然生效。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    created = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    ).json()["listing"]
    r = client.patch(
        f"/api/listings/{created['id']}",
        json={"parentAsin": "BAD-ASIN"},
        headers=auth_tokens["operator"],
    )
    assert r.status_code == 422


# ================================================================ 多对多


def test_material_in_multiple_listings(client, phase2_package, auth_users, auth_tokens, db_session):
    """5：一个素材（MAT）可以关联多个 Listing。"""
    task, ctx = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    batch = client.get(f"/api/design-packages/{ctx['pkg']['id']}/batches", headers=auth_tokens["manager"]).json()[0]
    mat_code = batch["variants"][0]["materialCode"]

    for url in (URL_A, "https://www.amazon.com/dp/B0TEST0002"):
        r = client.post(
            f"/api/distributions/{task['id']}/listings",
            json={"listingUrl": url},
            headers=auth_tokens["operator"],
        )
        assert r.status_code == 200, r.text

    r = client.get(f"/api/materials/{mat_code}/listings", headers=auth_tokens["manager"])
    assert r.status_code == 200, r.text
    assert len(r.json()) == 2, f"MAT 应被 2 个 Listing 使用，实际 {len(r.json())}"
    assert all(row["parentAsin"] is None for row in r.json())


def test_listing_has_multiple_materials(client, phase2_package, auth_users, auth_tokens, db_session):
    """6：一条 Listing 关联多个素材（自动挂任务快照的副素材 + 各自 MAT）。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens, variant_count=3)
    listing = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    ).json()["listing"]
    # 3 个副素材 + 3 个 MAT = 6 条关联
    assert listing["variantCount"] == 3
    assert listing["materialCount"] == 3

    # 再手动关联一个已有素材（幂等）
    r = client.post(
        f"/api/listings/{listing['id']}/materials",
        json={"variantId": task["items"][0]["variantId"]},
        headers=auth_tokens["manager"],
    )
    assert r.status_code == 200
    assert r.json()["variantCount"] == 3, "重复关联应幂等"

    db_session.rollback()
    rows = db_session.execute(select(ListingMaterial)).scalars().all()
    assert len(rows) == 6
    # 变体维度也能查到
    variant_listings = client.get(
        f"/api/material-variants/{task['items'][0]['variantId']}/listings",
        headers=auth_tokens["manager"],
    ).json()
    assert len(variant_listings) == 1


# ================================================================ 防重复


def test_duplicate_url_returns_existing_and_links(client, phase2_package, auth_users, auth_tokens, db_session):
    """7：同一 URL 重复录入 → 不新建，返回现有并自动关联当前任务素材。"""
    task_a, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    first = client.post(
        f"/api/distributions/{task_a['id']}/listings",
        json={"listingUrl": URL_A, "parentAsin": "B0PARENT01"},
        headers=auth_tokens["operator"],
    ).json()

    # 第二个任务（同一运营另一包）录同一 URL
    task_b, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    second = client.post(
        f"/api/distributions/{task_b['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    ).json()
    assert second["existed"] is True, "同 URL 应提示已存在"
    assert second["listing"]["id"] == first["listing"]["id"], "不重复造行"
    assert second["listing"]["parentAsin"] == "B0PARENT01", "现有 Listing 的 Parent 不被清掉"

    db_session.rollback()
    assert db_session.execute(select(func.count(Listing.id))).scalar_one() == 1
    # 现有 Listing 已自动挂上第二个任务的素材。
    # 注：phase2_package 生成相同字节 → 副素材被跨包去重复用 → 两任务素材 id 相同，
    # 因此关联按 (listing, material/variant) 去重后 = 2 副 + 2 主 = 4 条。
    links = db_session.execute(select(func.count(ListingMaterial.id))).scalar_one()
    assert links == 4, f"两任务素材应挂上同一 Listing（去重），实际 {links}"


# ================================================================ RBAC


def test_operator_only_own_task_listings(client, phase2_package, auth_users, auth_tokens):
    """8：OPERATOR 只能操作自己任务的 Listing；他人任务 404。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    # op_b 不能给 op_a 的任务加链接
    r = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator2"],
    )
    assert r.status_code == 404
    # op_a 可以
    r = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    )
    assert r.status_code == 200
    listing_id = r.json()["listing"]["id"]
    # op_b 不能查看/修改 op_a 的 Listing
    assert client.get(f"/api/listings/{listing_id}", headers=auth_tokens["operator2"]).status_code == 404
    r = client.patch(
        f"/api/listings/{listing_id}", json={"parentAsin": "B0HACK0000"}, headers=auth_tokens["operator2"]
    )
    assert r.status_code == 404


def test_manager_admin_view_all(client, phase2_package, auth_users, auth_tokens):
    """9：ADMIN / DESIGN_MANAGER 可查看与操作全部 Listing。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    created = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    ).json()["listing"]
    for headers in (auth_tokens["manager"], auth_tokens["admin"]):
        r = client.get(f"/api/listings/{created['id']}", headers=headers)
        assert r.status_code == 200
        assert client.get(f"/api/distributions/{task['id']}/listings", headers=headers).status_code == 200


def test_designer_cannot_operate_listings(client, phase2_package, auth_users, auth_tokens):
    """10：DESIGNER 不能操作 Listing（404，无任务访问权）。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    r = client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["designer"],
    )
    assert r.status_code == 404
    # 素材维度只读可见（DESIGNER 可看素材详情的上架信息）
    assert client.get(
        f"/api/material-variants/{task['items'][0]['variantId']}/listings",
        headers=auth_tokens["designer"],
    ).status_code == 200


# ================================================================ Child ASIN 与历史兼容


def test_listing_flow_does_not_create_child_asin_bindings(client, phase2_package, auth_users, auth_tokens, db_session):
    """12/13/14：Listing 流程不需要也不建立 Child ASIN → Variant/MAT 绑定。"""
    task, ctx = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    batch = client.get(f"/api/design-packages/{ctx['pkg']['id']}/batches", headers=auth_tokens["manager"]).json()[0]
    mat_code = batch["variants"][0]["materialCode"]

    client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    )
    db_session.rollback()
    # 没有任何 distribution_child_asins 行被创建
    assert db_session.execute(select(func.count(DistributionChildAsin.id))).scalar_one() == 0
    # 素材 ↔ Listing 关系只存在于 listing_materials
    assert db_session.execute(select(func.count(ListingMaterial.id))).scalar_one() > 0
    # MAT 维度 listings 查询正常（Child ASIN 不参与）
    assert len(client.get(f"/api/materials/{mat_code}/listings", headers=auth_tokens["manager"]).json()) == 1


def test_legacy_bind_data_survives_and_syncs_to_listings(client, phase2_package, auth_users, auth_tokens, db_session):
    """11/15：旧 bind_asins（Parent+Child）数据不丢；同步为 Listing 后 Contract 仍可用。"""
    from app.services.listing_service import sync_legacy_parent_listings

    task, ctx = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    child_asin = "B0LEGACY02"
    # 旧流程回填（会写 distribution_parent_asins + distribution_child_asins）
    old = client.put(
        f"/api/distributions/{task['id']}/asins",
        json={"parentAsin": "B0LEGACY01", "children": [child_asin]},
        headers=auth_tokens["operator"],
    )
    assert old.status_code == 200, old.text

    # 历史数据同步为 Listing（与 0013 migration 同口径；URL 空保留 NULL，不伪造）
    db_session.rollback()
    synced = sync_legacy_parent_listings(db_session)
    db_session.commit()
    assert synced == 1

    listings = client.get(f"/api/distributions/{task['id']}/listings", headers=auth_tokens["manager"]).json()
    assert len(listings) == 1
    legacy = listings[0]
    assert legacy["parentAsin"] == "B0LEGACY01", "Parent ASIN 原值保留"
    assert legacy["listingUrl"] is None, "历史无 URL 不伪造"
    assert legacy["parentAsinBoundAt"], "Parent 已有 → 记录绑定时间"
    # 自动关联了任务素材（2 副 + 2 主）
    assert legacy["variantCount"] == 2 and legacy["materialCount"] == 2
    # 幂等：再同步不重复
    db_session.rollback()
    assert sync_legacy_parent_listings(db_session) == 0
    db_session.commit()

    # 原 Contract 完全不受影响（仍按 Child ASIN 返回候选）
    c = client.get(f"/api/contract/materials-by-child-asin/{child_asin}")
    assert c.status_code == 200, c.text
    assert len(c.json()["variants"]) == 2
    # 旧表数据未被动过
    db_session.rollback()
    from app.db.models import DistributionParentAsin

    pa = db_session.execute(select(DistributionParentAsin)).scalars().all()
    assert len(pa) == 1 and pa[0].parent_asin == "B0LEGACY01"


def test_task_dto_lists_listing_count(client, phase2_package, auth_users, auth_tokens):
    """任务 DTO 带 listingCount（列表页展示用）。"""
    task, _ = _dispatched_task(client, phase2_package, auth_users, auth_tokens)
    assert client.get(f"/api/distributions/{task['id']}", headers=auth_tokens["manager"]).json()["listingCount"] == 0
    client.post(
        f"/api/distributions/{task['id']}/listings",
        json={"listingUrl": URL_A},
        headers=auth_tokens["operator"],
    )
    assert client.get(f"/api/distributions/{task['id']}", headers=auth_tokens["manager"]).json()["listingCount"] == 1
