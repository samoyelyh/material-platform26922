# -*- coding: utf-8 -*-
# ============================================================================
# P0：完全相同素材重复上传时，副素材不应重复创建（按 MAT + 内容去重）
# ============================================================================

from __future__ import annotations

from sqlalchemy import select

from app.db.models import Asset, Material, MaterialVariant, DerivativeBatch


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _create_batch(client, pkg_id: str, upload_id: str) -> dict:
    r = client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})
    assert r.status_code == 200, r.text
    return r.json()


def _build_package(client, create_package, create_upload, upload_file, name, main_bytes, variant_bytes):
    """建一个 1 主图 + 1 副图的设计包，生成 V1。返回 (pkg, batch_json)。"""
    pkg = create_package(name)
    up = create_upload(pkg["id"])
    upload_id = up["packageUpload"]["id"]
    main = upload_file(upload_id, main_bytes, "1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
    variant = upload_file(upload_id, variant_bytes, "1.png", "image/png", kind="VARIANT", file_role="VARIANT")
    sub = client.post(
        f"/api/uploads/{upload_id}/materials",
        json={
            "actor": "小柯",
            "materials": [{"position": 1, "previewAssetId": main["assetId"], "sourceFileName": main["originalFilename"]}],
            "linkAssetIds": [main["assetId"], variant["assetId"]],
        },
    )
    assert sub.status_code == 200, sub.text
    client.post(f"/api/uploads/{upload_id}/pair")
    _confirm_all(client, upload_id)
    batch = _create_batch(client, pkg["id"], upload_id)
    return pkg, batch


def test_identical_main_and_variant_does_not_duplicate_variant(
    client, create_package, create_upload, upload_file, png_bytes, db_session
):
    """Case 1：两包主副完全相同 → DesignPackage=2，Material=1，Variant=1，不出现两个 1-1。"""
    main_b = png_bytes((120, 30, 30))
    variant_b = png_bytes((30, 120, 30))

    pkgA, batchA = _build_package(client, create_package, create_upload, upload_file, "包A", main_b, variant_b)
    pkgB, batchB = _build_package(client, create_package, create_upload, upload_file, "包B", main_b, variant_b)

    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()

    # 设计包可以是 2 个（不同 DesignPackage）
    assert pkgA["id"] != pkgB["id"]
    # 但底层素材必须复用：只有 1 个 MAT
    assert len(materials) == 1, f"主素材应为 1 个，实际 {len(materials)}"
    # 副素材去重：只有 1 个 Variant（不能出现两个 1-1）
    assert len(variants) == 1, f"副素材应为 1 个，实际 {len(variants)}（重复创建！）"
    assert variants[0].display_code == "1-1"

    # 包 B 的 batch 应通过复用引用同一个 variant，而不是新建
    assert batchB["reusedVariantCount"] == 1, f"包B应复用副素材，实际 reused={batchB['reusedVariantCount']}"
    assert batchB["createdVariantCount"] == 0, f"包B不应新建副素材，实际 created={batchB['createdVariantCount']}"


def test_same_main_different_variant_creates_new_variant(
    client, create_package, create_upload, upload_file, png_bytes, db_session
):
    """Case 2：主素材相同、副素材不同 → 复用 MAT，新增真正不同的 Variant。"""
    main_b = png_bytes((120, 30, 30))
    variant_a = png_bytes((30, 120, 30))
    variant_b = png_bytes((30, 30, 120))

    _build_package(client, create_package, create_upload, upload_file, "包A", main_b, variant_a)
    _build_package(client, create_package, create_upload, upload_file, "包B", main_b, variant_b)

    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()
    assert len(materials) == 1, "同一 MAT 应复用"
    assert len(variants) == 2, f"副素材内容不同应各建一个，实际 {len(variants)}"


def test_different_main_and_variant_creates_new(
    client, create_package, create_upload, upload_file, png_bytes, db_session
):
    """Case 3：主副都不同 → 正常新建。"""
    _build_package(client, create_package, create_upload, upload_file, "包A", png_bytes((120, 30, 30)), png_bytes((30, 120, 30)))
    _build_package(client, create_package, create_upload, upload_file, "包B", png_bytes((50, 200, 50)), png_bytes((200, 50, 200)))

    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()
    assert len(materials) == 2
    assert len(variants) == 2
