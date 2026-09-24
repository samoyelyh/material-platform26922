"""users 表（登录 + RBAC）+ distribution_tasks.operator_user_id（派发身份化）

V1.1：引入最小内部账号体系。旧 operator_id / operator_name 字符串字段保留兼容期；
新任务必须绑定 operator_user_id → users.id。本迁移不含任何明文密码 / 默认账号。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0012_users_rbac"
down_revision: str | None = "0011_distribution_asin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_ARGS = {
    "mysql_engine": "InnoDB",
    "mysql_charset": "utf8mb4",
    "mysql_collate": "utf8mb4_0900_ai_ci",
}

NOW6 = sa.func.now(6)


def upgrade() -> None:
    bind = op.get_bind()

    # ------------------------------------------------ 1. users 表
    if not sa.inspect(bind).has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("username", mysql.VARCHAR(64), nullable=False),
            sa.Column("password_hash", mysql.VARCHAR(255), nullable=False),
            sa.Column("display_name", mysql.VARCHAR(128), nullable=False),
            sa.Column("role", mysql.VARCHAR(32), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("last_login_at", mysql.DATETIME(fsp=6), nullable=True),
            sa.UniqueConstraint("username", name="uq_users_username"),
            sa.CheckConstraint(
                "role IN ('ADMIN','DESIGN_MANAGER','DESIGNER','OPERATOR')",
                name="ck_users_role",
            ),
            comment="内部用户账号（登录 + RBAC）",
            **TABLE_ARGS,
        )
        op.create_index("ix_users_role_active", "users", ["role", "is_active"])

    # ------------------------------------------------ 2. distribution_tasks.operator_user_id
    cols = {c["name"] for c in sa.inspect(bind).get_columns("distribution_tasks")}
    if "operator_user_id" not in cols:
        op.add_column(
            "distribution_tasks",
            sa.Column("operator_user_id", mysql.VARCHAR(64), nullable=True),
        )
        op.create_index(
            "ix_distribution_tasks_operator_user",
            "distribution_tasks",
            ["operator_user_id"],
        )
    fks = {fk["name"] for fk in sa.inspect(bind).get_foreign_keys("distribution_tasks")}
    if "fk_distribution_tasks_operator_user" not in fks:
        op.create_foreign_key(
            "fk_distribution_tasks_operator_user",
            "distribution_tasks",
            "users",
            ["operator_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_constraint("fk_distribution_tasks_operator_user", "distribution_tasks", type_="foreignkey")
    op.drop_index("ix_distribution_tasks_operator_user", table_name="distribution_tasks")
    op.drop_column("distribution_tasks", "operator_user_id")
    op.drop_table("users")
