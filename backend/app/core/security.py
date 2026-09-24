# ============================================================================
# 密码哈希（bcrypt）+ JWT（PyJWT）
#
# 原则：
#   - 密码只存 bcrypt 哈希，绝不存明文，API/日志绝不输出 password_hash
#   - JWT SECRET 只从环境变量读，不写死进代码/提交
#   - token 只带 user_id / role / exp，不塞完整用户资料
# ============================================================================

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

# ---------------------------------------------------------------- 角色（RBAC 最小集）
ROLE_ADMIN = "ADMIN"
ROLE_DESIGN_MANAGER = "DESIGN_MANAGER"
ROLE_DESIGNER = "DESIGNER"
ROLE_OPERATOR = "OPERATOR"
ALL_ROLES = (ROLE_ADMIN, ROLE_DESIGN_MANAGER, ROLE_DESIGNER, ROLE_OPERATOR)


def _jwt_secret() -> str:
    """JWT 签名密钥：只从环境变量读。开发缺省时给一个固定占位并依赖部署覆盖。"""
    return os.getenv("JWT_SECRET", "material-center-dev-secret-change-me")


def jwt_expire_minutes() -> int:
    try:
        return int(os.getenv("JWT_EXPIRE_MINUTES", "720"))  # 默认 12 小时
    except ValueError:
        return 720


# ---------------------------------------------------------------- 密码哈希


def hash_password(plain: str) -> str:
    """bcrypt 哈希密码。只存哈希，绝不存明文。"""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, password_hash: str) -> bool:
    """校验密码（常量时间比较由 bcrypt 内部保证）。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- JWT


def create_access_token(user_id: str, role: str) -> str:
    """签发 JWT：只带 sub(user_id) / role / iat / exp。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=jwt_expire_minutes())).timestamp()),
    }
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def decode_access_token(token: str) -> dict | None:
    """解析 JWT。无效 / 过期返回 None。"""
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


__all__ = [
    "ALL_ROLES",
    "ROLE_ADMIN",
    "ROLE_DESIGNER",
    "ROLE_DESIGN_MANAGER",
    "ROLE_OPERATOR",
    "create_access_token",
    "decode_access_token",
    "hash_password",
    "jwt_expire_minutes",
    "verify_password",
]
