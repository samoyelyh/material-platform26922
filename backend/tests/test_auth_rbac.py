# -*- coding: utf-8 -*-
# ============================================================================
# V1.1 认证 + RBAC + 用户管理测试
# ============================================================================

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core.security import hash_password
from app.db.models import User


# ---------------------------------------------------------------- helpers


def _mkuser(db_session, username, role, password="pass123", display="测试", active=True):
    from app.services.repository import new_id

    u = User(
        id=new_id("user"),
        username=username,
        password_hash=hash_password(password),
        display_name=display,
        role=role,
        is_active=active,
    )
    db_session.add(u)
    db_session.commit()
    return u


def _login(client, username, password="pass123"):
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _dispatch_pkg(client, phase2_package, token):
    """建一个有 V1 的设计包（用管理角色），返回 pkg_id。"""
    ctx = phase2_package(2)
    client.post(f"/api/uploads/{ctx['upload_id']}/pairings/confirm", params={"actor": "小柯"})
    r = client.post(f"/api/design-packages/{ctx['pkg']['id']}/batches", json={"actor": "阿May"})
    assert r.status_code == 200, r.text
    return ctx["pkg"]["id"]


# ---------------------------------------------------------------- Auth


def test_login_success(client, db_session):
    _mkuser(db_session, "admin", "ADMIN")
    r = client.post("/api/auth/login", json={"username": "admin", "password": "pass123"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token"]
    assert body["user"]["username"] == "admin"
    assert body["user"]["role"] == "ADMIN"
    assert "password" not in body["user"] and "passwordHash" not in body["user"]


def test_login_wrong_password(client, db_session):
    _mkuser(db_session, "admin", "ADMIN")
    r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["code"] == "UNAUTHORIZED"


def test_login_user_not_found(client, db_session):
    r = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
    assert r.status_code == 401


def test_login_disabled_user(client, db_session):
    _mkuser(db_session, "off", "OPERATOR", active=False)
    r = client.post("/api/auth/login", json={"username": "off", "password": "pass123"})
    assert r.status_code == 401


def test_me_invalid_token(client, db_session):
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_me_expired_or_tampered_token(client, db_session):
    import jwt as pyjwt

    _mkuser(db_session, "admin", "ADMIN")
    bad = pyjwt.encode({"sub": "x", "role": "ADMIN", "exp": 1}, "wrong-secret", algorithm="HS256")
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad}"})
    assert r.status_code == 401


def test_me_success_and_disabled_after(client, db_session):
    u = _mkuser(db_session, "admin", "ADMIN")
    token = _login(client, "admin")
    r = client.get("/api/auth/me", headers=_hdr(token))
    assert r.status_code == 200
    assert r.json()["username"] == "admin"
    # 禁用后 /me 也拒绝
    u.is_active = False
    db_session.commit()
    r2 = client.get("/api/auth/me", headers=_hdr(token))
    assert r2.status_code == 403


# ---------------------------------------------------------------- User 管理（ADMIN）


def test_admin_create_and_unique_username(client, db_session):
    _mkuser(db_session, "admin", "ADMIN")
    token = _login(client, "admin")
    r = client.post(
        "/api/users",
        headers=_hdr(token),
        json={"username": "op1", "displayName": "运营一", "role": "OPERATOR", "password": "abc123"},
    )
    assert r.status_code == 200, r.text
    # username 唯一
    r2 = client.post(
        "/api/users",
        headers=_hdr(token),
        json={"username": "op1", "displayName": "运营二", "role": "OPERATOR", "password": "abc123"},
    )
    assert r2.status_code == 422


def test_non_admin_cannot_manage_users(client, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    token = _login(client, "dm")
    r = client.get("/api/users", headers=_hdr(token))
    assert r.status_code == 403


def test_admin_patch_role_active_password(client, db_session):
    _mkuser(db_session, "admin", "ADMIN")
    op = _mkuser(db_session, "op1", "OPERATOR")
    token = _login(client, "admin")
    # 改角色
    r = client.patch(f"/api/users/{op.id}", headers=_hdr(token), json={"role": "DESIGNER"})
    assert r.status_code == 200 and r.json()["role"] == "DESIGNER"
    # 禁用
    r2 = client.patch(f"/api/users/{op.id}", headers=_hdr(token), json={"isActive": False})
    assert r2.json()["isActive"] is False
    # 重置密码 → 新密码可登录
    client.patch(f"/api/users/{op.id}", headers=_hdr(token), json={"isActive": True, "password": "newpass1"})
    assert _login(client, "op1", "newpass1")


def test_operators_list_only_active_operator(client, db_session):
    _mkuser(db_session, "admin", "ADMIN")
    _mkuser(db_session, "op1", "OPERATOR", display="运营一")
    _mkuser(db_session, "op2", "OPERATOR", active=False, display="运营二")
    _mkuser(db_session, "des", "DESIGNER")
    token = _login(client, "admin")
    r = client.get("/api/users/operators", headers=_hdr(token))
    names = [o["displayName"] for o in r.json()]
    assert names == ["运营一"]


# ---------------------------------------------------------------- RBAC：Distribution


def test_designer_cannot_create_distribution(client, phase2_package, db_session):
    _mkuser(db_session, "des", "DESIGNER")
    _mkuser(db_session, "op1", "OPERATOR")
    op_id = db_session.execute(select(User.id).where(User.username == "op1")).scalar_one()
    token = _login(client, "des")
    pkg_id = _dispatch_pkg(client, phase2_package, token)
    r = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(token), json={"operatorUserId": op_id})
    assert r.status_code == 403


def test_manager_can_create_distribution(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR", display="运营一")
    op = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    token = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, token)
    r = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(token), json={"operatorUserId": op.id})
    assert r.status_code == 200, r.text
    assert r.json()["operatorUserId"] == op.id
    assert r.json()["operatorName"] == "运营一"


def test_operator_sees_only_own_tasks(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR")
    _mkuser(db_session, "op2", "OPERATOR")
    op1 = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    op2 = db_session.execute(select(User).where(User.username == "op2")).scalar_one()
    dm = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, dm)
    client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(dm), json={"operatorUserId": op1.id})
    tok1 = _login(client, "op1")
    tok2 = _login(client, "op2")
    mine = client.get("/api/distributions/my", headers=_hdr(tok1))
    assert len(mine.json()) == 1
    other = client.get("/api/distributions/my", headers=_hdr(tok2))
    assert other.json() == []


def test_operator_cannot_view_others_task_detail(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR")
    _mkuser(db_session, "op2", "OPERATOR")
    op1 = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    dm = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, dm)
    task = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(dm), json={"operatorUserId": op1.id}).json()
    tok2 = _login(client, "op2")
    r = client.get(f"/api/distributions/{task['id']}", headers=_hdr(tok2))
    assert r.status_code == 404


def test_operator_receive_own_task(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR")
    op1 = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    dm = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, dm)
    task = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(dm), json={"operatorUserId": op1.id}).json()
    tok1 = _login(client, "op1")
    r = client.post(f"/api/distributions/{task['id']}/receive", headers=_hdr(tok1))
    assert r.status_code == 200 and r.json()["status"] == "RECEIVED"


def test_operator_cannot_receive_others_task(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR")
    _mkuser(db_session, "op2", "OPERATOR")
    op1 = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    dm = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, dm)
    task = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(dm), json={"operatorUserId": op1.id}).json()
    tok2 = _login(client, "op2")
    r = client.post(f"/api/distributions/{task['id']}/receive", headers=_hdr(tok2))
    assert r.status_code == 404


def test_operator_bind_asin_own_task(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR")
    op1 = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    dm = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, dm)
    task = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(dm), json={"operatorUserId": op1.id}).json()
    tok1 = _login(client, "op1")
    client.post(f"/api/distributions/{task['id']}/receive", headers=_hdr(tok1))
    r = client.put(f"/api/distributions/{task['id']}/asins", headers=_hdr(tok1), json={"parentAsin": "B0PARENT01", "children": ["B0CHILD000"]})
    assert r.status_code == 200 and r.json()["status"] == "COMPLETED"


def test_operator_cannot_cancel(client, phase2_package, db_session):
    _mkuser(db_session, "dm", "DESIGN_MANAGER")
    _mkuser(db_session, "op1", "OPERATOR")
    op1 = db_session.execute(select(User).where(User.username == "op1")).scalar_one()
    dm = _login(client, "dm")
    pkg_id = _dispatch_pkg(client, phase2_package, dm)
    task = client.post(f"/api/design-packages/{pkg_id}/distributions", headers=_hdr(dm), json={"operatorUserId": op1.id}).json()
    tok1 = _login(client, "op1")
    r = client.post(f"/api/distributions/{task['id']}/cancel", headers=_hdr(tok1))
    assert r.status_code == 403
    # 管理角色可取消
    r2 = client.post(f"/api/distributions/{task['id']}/cancel", headers=_hdr(dm))
    assert r2.status_code == 200
