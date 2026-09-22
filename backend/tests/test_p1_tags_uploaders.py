# -*- coding: utf-8 -*-
# ============================================================================
# P1：标签同步（Package → MAT + Variant）+ 筛选选项（真实上传人/标签）
# ============================================================================

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Material, MaterialVariant


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _create_batch(client, pkg_id: str, upload_id: str) -> dict:
    r = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})
    assert r.status_code == 200, r.text
    return r.json()


def test_package_tags_sync_to_material_and_variant(client, phase2_package, db_session):
    """给设计包新增标签 → 包内 MAT 与 Variant 都同步拥有（一次性新增同步）。"""
    ctx = phase2_package(2, tags=["NFL"], design_code="DS-TAG-001")
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    _create_batch(client, pkg_id, ctx["upload_id"])

    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()
    assert materials and variants
    # 创建时继承：MAT 与 Variant 都有 NFL
    assert all("NFL" in (m.tags or []) for m in materials)
    assert all("NFL" in (v.tags or []) for v in variants)

    # 新增标签 黑色 + syncTagsToMaterials → MAT 与 Variant 都获得
    r = client.patch(
        f"/api/design-packages/{pkg_id}",
        json={"tags": ["NFL", "黑色"], "syncTagsToMaterials": True, "designerName": "阿May"},
    )
    assert r.status_code == 200, r.text

    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()
    assert all("黑色" in (m.tags or []) for m in materials), "MAT 应同步获得新标签 黑色"
    assert all("黑色" in (v.tags or []) for v in variants), "Variant 应同步获得新标签 黑色（之前只同步主素材）"


def test_filter_options_returns_real_uploaders_and_tags(client, phase2_package, db_session):
    """filter-options 返回真实存在的上传人与标签，不写死。"""
    ctx = phase2_package(2, tags=["NFL"], design_code="DS-FO-001")
    _confirm_all(client, ctx["upload_id"])
    _create_batch(client, ctx["pkg"]["id"], ctx["upload_id"])

    r = client.get("/api/filter-options")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "uploaders" in body and "tags" in body
    # 上传人来自真实数据（本次上传的实际上传人；fixture 为「实际上传人-小柯」）
    assert any("小柯" in u for u in body["uploaders"]), f"uploaders 应含真实上传人: {body['uploaders']}"
    # 标签来自真实数据（NFL）
    assert "NFL" in body["tags"], f"tags 应含真实标签: {body['tags']}"


def test_manual_material_tag_is_queryable_via_filter_options(client, phase2_package, db_session):
    """手动给 MAT 打标签后，该标签出现在筛选选项中（真实标签，不是写死）。"""
    ctx = phase2_package(1, design_code="DS-MT-001")
    _confirm_all(client, ctx["upload_id"])
    _create_batch(client, ctx["pkg"]["id"], ctx["upload_id"])

    material = db_session.execute(select(Material)).scalars().first()
    # 手动给 MAT 加标签
    r = client.patch(
        f"/api/materials/{material.material_code}/tags",
        json={"tags": ["球迷主题"], "actor": "素材中心"},
    )
    assert r.status_code == 200, r.text

    opts = client.get("/api/filter-options").json()
    assert "球迷主题" in opts["tags"], "手动新增的 MAT 标签应出现在筛选选项"
