# -*- coding: utf-8 -*-
# ============================================================================
# P2：Material / Variant 流转记录按实体过滤 + 降噪
# ============================================================================

from __future__ import annotations

LOW_VALUE = {"CREATE_ASSET", "REUSE_ASSET", "UPLOAD_FAILED", "LINK_ASSET", "IMAGE_INDEX_FAILED", "IMAGE_REINDEXED"}


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _create_batch(client, pkg_id: str, upload_id: str) -> dict:
    r = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_material_history_is_entity_centric_and_denoised(client, phase2_package, db_session):
    """MAT 流转记录：只含自身 + 其副素材 + 相关 Batch/Distribution，不含低价值内部事件、不含其它素材事件。"""
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    batch = _create_batch(client, pkg_id, ctx["upload_id"])
    # 派发 + 接收（产生 DISTRIBUTION 事件）
    task = client.post(
        f"/api/design-packages/{pkg_id}/distributions",
        json={"operatorId": "zhang", "operatorName": "张三", "actor": "肖芸"},
    ).json()
    client.post(f"/api/distributions/{task['id']}/receive")

    # 取一个 MAT
    from sqlalchemy import select

    from app.db.models import Material

    material = db_session.execute(select(Material)).scalars().first()
    r = client.get(f"/api/materials/{material.material_code}/history")
    assert r.status_code == 200, r.text
    logs = r.json()
    assert logs, "应至少有 MAT 创建事件"
    actions = [log["action"] for log in logs]

    # 不含低价值内部事件
    assert not LOW_VALUE.intersection(actions), f"不应含低价值事件: {LOW_VALUE.intersection(actions)}"
    # 含 MAT 创建 + Batch + Distribution 关键事件
    assert "CREATE_MATERIAL" in actions
    assert "CREATE_BATCH" in actions
    assert "DISPATCH" in actions

    # 不含「其它素材」的专属事件（这里只有一个包两个素材，校验 target_id 都归本 MAT 或其副素材）
    mat_target_ids = {log["targetId"] for log in logs if log["targetType"] == "MATERIAL"}
    assert mat_target_ids == {material.id}, f"MAT 事件 target 应只有本 MAT: {mat_target_ids}"


def test_variant_history_includes_own_batch_distribution(client, phase2_package, db_session):
    """Variant 流转记录：含自身 + Batch + Distribution 关键事件，不含低价值内部事件。"""
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    batch = _create_batch(client, pkg_id, ctx["upload_id"])
    vid = batch["variants"][0]["id"]

    task = client.post(
        f"/api/design-packages/{pkg_id}/distributions",
        json={"operatorId": "zhang", "operatorName": "张三", "actor": "肖芸"},
    ).json()
    client.post(f"/api/distributions/{task['id']}/receive")

    r = client.get(f"/api/material-variants/{vid}/history")
    assert r.status_code == 200, r.text
    logs = r.json()
    assert logs
    actions = [log["action"] for log in logs]
    assert not LOW_VALUE.intersection(actions)
    assert "CREATE_VARIANT" in actions
    assert "CREATE_BATCH" in actions
    assert "DISPATCH" in actions
    # 自身事件 target 都是这个 variant
    own = [log for log in logs if log["targetType"] == "MATERIAL_VARIANT"]
    assert all(log["targetId"] == vid for log in own)
