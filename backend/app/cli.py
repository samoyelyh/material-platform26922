# ============================================================================
# 运维 CLI
#
#   python -m app.cli create-admin
#     交互式创建第一个管理员账号（密码交互输入，不回显、不打印、不入库明文、不提交）。
#
# 用法（部署环境）：
#   docker exec -it mp-backend python -m app.cli create-admin
# ============================================================================

from __future__ import annotations

import getpass
import sys

from sqlalchemy import select

from app.core.security import ROLE_ADMIN, hash_password
from app.db.models import User
from app.db.session import SessionLocal
from app.services.repository import new_id


def create_admin() -> int:
    username = input("管理员账号 username: ").strip()
    if not username:
        print("账号不能为空")
        return 1
    display_name = input("显示姓名 display_name: ").strip()
    if not display_name:
        print("姓名不能为空")
        return 1
    password = getpass.getpass("密码（不回显）: ")
    password2 = getpass.getpass("再输一次: ")
    if password != password2:
        print("两次输入不一致")
        return 1
    if len(password) < 6:
        print("密码至少 6 位")
        return 1

    db = SessionLocal()
    try:
        existing = db.execute(select(User).where(User.username == username)).scalar_one_or_none()
        if existing is not None:
            # 已存在 → 重置为管理员并更新密码/启用
            existing.role = ROLE_ADMIN
            existing.display_name = display_name
            existing.password_hash = hash_password(password)
            existing.is_active = True
            db.commit()
            print(f"已更新为管理员并启用：{username}（{display_name}）")
            return 0
        user = User(
            id=new_id("user"),
            username=username,
            password_hash=hash_password(password),
            display_name=display_name,
            role=ROLE_ADMIN,
            is_active=True,
        )
        db.add(user)
        db.commit()
        print(f"已创建管理员：{username}（{display_name}）")
        return 0
    finally:
        db.close()


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "create-admin":
        return create_admin()
    print("用法: python -m app.cli create-admin")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
