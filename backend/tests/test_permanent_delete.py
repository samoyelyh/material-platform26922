# -*- coding: utf-8 -*-
# ============================================================================
# 永久删除设计包：物理删除自身数据，保留共享 MAT / Asset；被其它包引用时拒绝
# ============================================================================

from __future__ import annotations

from sqlalchemy import select

from app.db.models import (
    Asset,
    DerivativeBatch,
    DesignPackage,
    DesignPackageMaterial,
    DistributionTask,
    Material,
    MaterialVariant,
    PackageUpload,
    VariantEffectImage,
)


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _create_batch(client, pkg_id: str, upload_id: str) -> dict:
    r = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})
    assert r.status_code == 200, r.text
    return r.json()


def _count(db, model):
    return db.execute(select(model)).scalars().all()


def test_permanent_delete_removes_own_data_keeps_shared_mat_asset(client, phase2_package, db_session):
    """单包永久删除：包自身数据全删；共享 MAT / Asset 保留。"""
    ctx = phase2_package(2)
    pkg_id = ctx["pkg"]["id"]
    _confirm_all(client, ctx["upload_id"])
    batch = _create_batch(client, pkg_id, ctx["upload_id"])
    # 派发 + ASIN
    task = client.post(
        f"/api/design-packages/{pkg_id}/distributions",
        json={"operatorId": "zhang", "operatorName": "张三", "actor": "肖芸"},
    ).json()
    client.put(f"/api/distributions/{task['id']}/asins", json={"parentAsin": "B0PARENT01", "children": ["B0CHILD000"]})

    db_session.rollback()
    mat_count_before = len(_count(db_session, Material))
    asset_count_before = len(_count(db_session, Asset))

    r = client.delete(f"/api/design-packages/{pkg_id}/permanent?actor=收口")
    assert r.status_code == 200, r.text

    db_session.rollback()
    assert _count(db_session, DesignPackage) == [], "设计包应被物理删除"
    assert _count(db_session, DesignPackageMaterial) == [], "位置应删除"
    assert _count(db_session, DerivativeBatch) == [], "批次应删除"
    assert _count(db_session, MaterialVariant) == [], "副素材应删除"
    assert _count(db_session, PackageUpload) == [], "上传记录应删除"
    assert _count(db_session, DistributionTask) == [], "派发任务应删除"
    # 共享 MAT / Asset 保留
    assert len(_count(db_session, Material)) == mat_count_before, "共享 MAT 应保留"
    assert len(_count(db_session, Asset)) == asset_count_before, "共享 Asset 应保留"

    # 详情 404
    assert client.get(f"/api/design-packages/{pkg_id}").status_code == 404


def _build_two_identical_packages(client, create_package, create_upload, upload_file, png_bytes):
    main_b = png_bytes((120, 30, 30))
    variant_b = png_bytes((30, 120, 30))

    def build(name):
        pkg = create_package(name)
        up = create_upload(pkg["id"])
        upload_id = up["packageUpload"]["id"]
        m = upload_file(upload_id, main_b, "1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
        v = upload_file(upload_id, variant_b, "1.png", "image/png", kind="VARIANT", file_role="VARIANT")
        sub = client.post(
            f"/api/uploads/{upload_id}/materials",
            json={
                "actor": "小柯",
                "materials": [{"position": 1, "previewAssetId": m["assetId"], "sourceFileName": m["originalFilename"]}],
                "linkAssetIds": [m["assetId"], v["assetId"]],
            },
        )
        assert sub.status_code == 200, sub.text
        client.post(f"/api/uploads/{upload_id}/pair")
        _confirm_all(client, upload_id)
        _create_batch(client, pkg["id"], upload_id)
        return pkg["id"]

    return build("包A"), build("包B")


def test_permanent_delete_refused_when_variant_referenced_by_other_package(
    client, create_package, create_upload, upload_file, png_bytes, db_session
):
    """包A副素材被包B复用引用时：删包A → 409；删包B → 成功（包A素材保留）。"""
    pkgA, pkgB = _build_two_identical_packages(client, create_package, create_upload, upload_file, png_bytes)

    # 包A 的副素材被包B（去重复用配对）引用 → 删包A 应 409
    r = client.delete(f"/api/design-packages/{pkgA}/permanent")
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "PACKAGE_REFERENCED"

    # 删包B（它引用包A副素材但不拥有）→ 成功；包A素材保留
    db_session.rollback()
    variants_before = len(_count(db_session, MaterialVariant))
    r2 = client.delete(f"/api/design-packages/{pkgB}/permanent")
    assert r2.status_code == 200, r2.text
    db_session.rollback()
    assert _count(db_session, DesignPackage) != []
    assert len(_count(db_session, MaterialVariant)) == variants_before, "包A副素材应保留"

    # 此时包A不再被其它包引用 → 可删
    r3 = client.delete(f"/api/design-packages/{pkgA}/permanent")
    assert r3.status_code == 200, r3.text
    db_session.rollback()
    assert len(_count(db_session, Material)) == 1, "共享 MAT 仍保留"
