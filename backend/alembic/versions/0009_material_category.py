"""design_packages 补 category_code / category_name（素材域品类）

拆分时订单迁移 0009（含 category_code 加列 + 订单表）移出 material-platform；
但 design_packages.category_code 是**素材域能力**（订单中心依赖它做品类隔离契约），
因此这里在素材域迁移链上单独重建这两列。订单表不属于素材库。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0009_material_category"
down_revision: str | None = "0008_asset_image_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("design_packages")}
    if "category_code" not in cols:
        op.add_column(
            "design_packages",
            sa.Column("category_code", mysql.VARCHAR(64), nullable=False, server_default="UNKNOWN"),
        )
        op.add_column(
            "design_packages",
            sa.Column("category_name", mysql.VARCHAR(128), nullable=False, server_default="未知品类"),
        )
        op.create_index("ix_design_packages_category", "design_packages", ["category_code"])
        op.alter_column("design_packages", "category_code", server_default=None)
        op.alter_column("design_packages", "category_name", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_design_packages_category", table_name="design_packages")
    op.drop_column("design_packages", "category_name")
    op.drop_column("design_packages", "category_code")
