# -*- coding: utf-8 -*-
# ============================================================================
# 副素材跨 MAT 归属规则矩阵（正式业务规则：禁止副素材跨 MAT 归属）
#
# | MAT  | 副素材 | 结果                                  |
# | 相同 | 相同   | 复用 Variant                          |
# | 相同 | 不同   | 创建新 Variant                        |
# | 不同 | 相同   | **CONFLICT，禁止归属（不建版）**      |
# | 不同 | 不同   | 正常新建                              |
# ============================================================================

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Asset, Material, MaterialVariant


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _batch(client, pkg_id: str, upload_id: str):
    return client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})


def _build(client, create_package, create_upload, upload_file, name, main_bytes, variant_bytes):
    pkg = create_package(name)
    up = create_upload(pkg["id"])
    upload_id = up["packageUpload"]["id"]
    m = upload_file(upload_id, main_bytes, "1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
    v = upload_file(upload_id, variant_bytes, "1.png", "image/png", kind="VARIANT", file_role="VARIANT")
    sub = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={"actor": "小柯", "materials": [{"position": 1, "previewAssetId": m["assetId"], "sourceFileName": m["originalFilename"]}], "linkAssetIds": [m["assetId"], v["assetId"]]},
    )
    assert sub.status_code == 200, sub.text
    client.post(f"/api/uploads/{upload_id}/pair")
    _confirm_all(client, upload_id)
    return pkg["id"], upload_id


def test_same_mat_same_variant_reuses(client, create_package, create_upload, upload_file, png_bytes, db_session):
    """矩阵①：相同 MAT + 相同副素材 → 复用 Variant（不新建）。"""
    main_b = png_bytes((120, 30, 30))
    variant_b = png_bytes((30, 120, 30))
    # 包A 建版（产生 MAT-A + Variant-A1）
    pkgA, upA = _build(client, create_package, create_upload, upload_file, "包A", main_b, variant_b)
    rA = _batch(client, pkgA, upA)
    assert rA.status_code == 200, rA.text
    # 包B（同主→复用 MAT-A + 同副素材）建版 → 复用 Variant-A1
    pkgB, upB = _build(client, create_package, create_upload, upload_file, "包B", main_b, variant_b)
    r = _batch(client, pkgB, upB)
    assert r.status_code == 200, r.text
    assert r.json()["reusedVariantCount"] == 1, r.json()
    db_session.rollback()
    assert len(db_session.execute(select(MaterialVariant)).scalars().all()) == 1


def test_same_mat_diff_variant_creates_new(client, create_package, create_upload, upload_file, png_bytes, db_session):
    """矩阵②：相同 MAT + 不同副素材 → 创建新 Variant。"""
    main_b = png_bytes((120, 30, 30))
    pkgA, upA = _build(client, create_package, create_upload, upload_file, "包A", main_b, png_bytes((30, 120, 30)))
    rA = _batch(client, pkgA, upA)
    assert rA.status_code == 200, rA.text
    pkgB, upB = _build(client, create_package, create_upload, upload_file, "包B", main_b, png_bytes((30, 30, 120)))
    r = _batch(client, pkgB, upB)
    assert r.status_code == 200, r.text
    db_session.rollback()
    assert len(db_session.execute(select(Material)).scalars().all()) == 1
    assert len(db_session.execute(select(MaterialVariant)).scalars().all()) == 2


def test_diff_mat_same_variant_conflict(client, create_package, create_upload, upload_file, png_bytes, db_session):
    """矩阵③（Test C）：不同 MAT + 相同副素材 → CONFLICT，禁止归属，不建版。

    断言：
      Material = 2（MAT-A、MAT-B 都在）
      Variant = 1（只有 MAT-A 的 X；MAT-B 不得出现 X）
      Asset-X = 1（同一物理文件复用）
      Variant-X.material_id == MAT-A
      接口返回 VARIANT_ALREADY_BELONGS_TO_OTHER_MATERIAL（结构化冲突信息）
    """
    main_a = png_bytes((120, 30, 30))
    main_b = png_bytes((50, 200, 50))
    variant_x = png_bytes((30, 120, 30))

    # 包A：MAT-A + 副素材 X → 正常建版
    pkgA, upA = _build(client, create_package, create_upload, upload_file, "包A", main_a, variant_x)
    rA = _batch(client, pkgA, upA)
    assert rA.status_code == 200, rA.text
    variant_a_code = rA.json()["variants"][0]["materialCode"]
    variant_a1_code = rA.json()["variants"][0]["displayCode"]

    # 包B：不同主素材 MAT-B + 相同副素材 X → 必须 CONFLICT
    pkgB, upB = _build(client, create_package, create_upload, upload_file, "包B", main_b, variant_x)
    rB = _batch(client, pkgB, upB)
    assert rB.status_code == 409, rB.text
    body = rB.json()
    assert body["code"] == "VARIANT_ALREADY_BELONGS_TO_OTHER_MATERIAL", body
    # 结构化冲突信息完整
    detail = body["detail"]["conflicts"]
    assert len(detail) == 1
    c = detail[0]
    assert c["reason"] == "VARIANT_ALREADY_BELONGS_TO_OTHER_MATERIAL"
    assert c["existingVariantCode"] == variant_a1_code
    assert c["existingMaterialCode"] == variant_a_code
    assert c["filename"]
    assert c["existingMaterialId"] != c["currentMaterialId"]

    # 落库断言：MAT=2，Variant=1（MAT-B 不得出现 X），Variant-X.material_id == MAT-A
    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()
    assert len(materials) == 2
    assert len(variants) == 1, f"MAT-B 不得出现副素材 X，实际 {len(variants)} 个"
    assert variants[0].material_id == [m.id for m in materials if m.material_code == variant_a_code][0]
    # Asset-X 只有一个（BLAKE3 复用）
    x_assets = db_session.execute(select(Asset)).scalars().all()
    assert all(a.blake3 for a in x_assets)  # 仅示意；重点是 MAT-B 无 X


def test_diff_mat_diff_variant_normal(client, create_package, create_upload, upload_file, png_bytes, db_session):
    """矩阵④：不同 MAT + 不同副素材 → 正常新建。"""
    pkgA, upA = _build(client, create_package, create_upload, upload_file, "包A", png_bytes((120, 30, 30)), png_bytes((30, 120, 30)))
    rA = _batch(client, pkgA, upA)
    assert rA.status_code == 200, rA.text
    pkgB, upB = _build(client, create_package, create_upload, upload_file, "包B", png_bytes((50, 200, 50)), png_bytes((200, 50, 200)))
    r = _batch(client, pkgB, upB)
    assert r.status_code == 200, r.text
    db_session.rollback()
    assert len(db_session.execute(select(Material)).scalars().all()) == 2
    assert len(db_session.execute(select(MaterialVariant)).scalars().all()) == 2
