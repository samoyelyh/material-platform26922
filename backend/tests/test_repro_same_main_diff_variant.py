# -*- coding: utf-8 -*-
"""复现：相同主素材 + 不同副素材 的第二包上传失败（设计包不存在）。"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import Material, MaterialVariant


def _confirm_all(client, upload_id: str):
    r = client.post(f"/api/uploads/{upload_id}/pairings/confirm", params={"actor": "小柯"})
    assert r.status_code == 200, r.text


def _create_batch(client, pkg_id: str, upload_id: str):
    return client.post(f"/api/design-packages/{pkg_id}/batches", json={"actor": "阿May", "uploadId": upload_id})


def test_same_main_different_variant_second_package_succeeds(
    client, create_package, create_upload, upload_file, png_bytes, db_session
):
    """包A(主A+副A1) 与 包B(主A+副A2)：第二包上传+建版应成功，不应「设计包不存在」。"""
    main_b = png_bytes((120, 30, 30))
    variant_a = png_bytes((30, 120, 30))
    variant_b = png_bytes((30, 30, 120))

    def build(pkg_name, variant_bytes):
        pkg = create_package(pkg_name)
        up = create_upload(pkg["id"])
        upload_id = up["packageUpload"]["id"]
        m = upload_file(upload_id, main_b, "1.png", "image/png", kind="MAIN", file_role="MAIN_PREVIEW")
        v = upload_file(upload_id, variant_bytes, "1.png", "image/png", kind="VARIANT", file_role="VARIANT")
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
        return pkg["id"], upload_id

    pkgA, uploadA = build("包A", variant_a)
    rA = _create_batch(client, pkgA, uploadA)
    assert rA.status_code == 200, rA.text

    # 第二包：相同主素材 + 不同副素材
    pkgB, uploadB = build("包B", variant_b)
    rB = _create_batch(client, pkgB, uploadB)
    assert rB.status_code == 200, f"第二包建版失败：{rB.status_code} {rB.text}"

    db_session.rollback()
    materials = db_session.execute(select(Material)).scalars().all()
    variants = db_session.execute(select(MaterialVariant)).scalars().all()
    assert len(materials) == 1, "同一主素材应复用为 1 个 MAT"
    assert len(variants) == 2, f"副素材不同应各建一个，实际 {len(variants)}"
