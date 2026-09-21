"""variant_effect_images 表（素材域：Variant 效果图角色）

拆分时订单迁移 0010（含 variant_effect_images）移出 material-platform；
但 variant_effect_images 是 **素材域能力**（Variant 的 MATERIAL_SOURCE /
FINAL_EFFECT Black/White 图片角色，订单中心依赖它做最终效果图匹配契约），
因此这里在素材域迁移链上单独建表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0010_variant_effect_images"
down_revision: str | None = "0009_material_category"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not op.get_bind().dialect.has_table(op.get_bind(), "variant_effect_images"):
        op.create_table(
            "variant_effect_images",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("image_role", mysql.VARCHAR(32), nullable=False),
            sa.Column("sole_color", mysql.VARCHAR(16), nullable=True),
            sa.Column("asset_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("source_url", mysql.VARCHAR(512), nullable=True),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=sa.func.now(6), nullable=False),
            sa.ForeignKeyConstraint(["variant_id"], ["material_variants.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], ondelete="SET NULL"),
            sa.CheckConstraint(
                "image_role IN ('MATERIAL_SOURCE','FINAL_EFFECT','PREVIEW_ONLY')",
                name="ck_variant_effect_images_role",
            ),
            sa.UniqueConstraint("variant_id", "image_role", "sole_color", name="uq_variant_effect_role"),
            mysql_engine="InnoDB",
            mysql_charset="utf8mb4",
            mysql_collate="utf8mb4_0900_ai_ci",
        )


def downgrade() -> None:
    op.drop_table("variant_effect_images")
