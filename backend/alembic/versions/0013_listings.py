"""listings / listing_materials（素材 ↔ Listing URL 多对多）

业务原则（本轮）：
  - Listing URL 是上架记录的核心数据；Parent ASIN 是 Listing 的后补属性（可空）
  - 一条 Listing 可挂多个素材；一个素材可被多个 Listing 使用（M2M）
  - 一个 DistributionTask 可产生多个 Listing
  - Child ASIN 不再参与素材归属（旧表保留兼容，Contract 不动）

历史数据：distribution_parent_asins 的存量行**复制**进 listings
（id=listing-{旧id} 幂等可重跑；不覆盖原表；不伪造 URL——listing_url 为空就存 NULL；
parent_asin 原值保留；并按任务快照自动建立 Listing ↔ Variant/MAT 关联）。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0013_listings"
down_revision: str | None = "0012_users_rbac"
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
    inspector = sa.inspect(bind)

    # ------------------------------------------------ 1. listings
    if not inspector.has_table("listings"):
        op.create_table(
            "listings",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("distribution_task_id", mysql.VARCHAR(64), nullable=False),
            # Listing URL 是上架核心；历史迁移行可能为 NULL（不伪造 URL），新数据由 API 强制必填
            sa.Column("listing_url", mysql.VARCHAR(2048), nullable=True),
            # Parent ASIN 是后补属性：可为空，运营拿到后 PATCH 更新原行（不新建第二条）
            sa.Column("parent_asin", mysql.VARCHAR(32), nullable=True),
            sa.Column("store", mysql.VARCHAR(128), nullable=True),
            sa.Column("site", mysql.VARCHAR(8), nullable=True),
            sa.Column("operator_user_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("created_by", mysql.VARCHAR(128), nullable=False),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("parent_asin_bound_at", mysql.DATETIME(fsp=6), nullable=True),
            sa.ForeignKeyConstraint(
                ["distribution_task_id"],
                ["distribution_tasks.id"],
                name="fk_listings_task",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["operator_user_id"],
                ["users.id"],
                name="fk_listings_operator_user",
                ondelete="SET NULL",
            ),
            comment="上架 Listing（URL 为核心，Parent ASIN 后补；一任务可多条）",
            **TABLE_ARGS,
        )
        # 防重复兜底：同一 URL 只允许一条 Listing。
        # URL 2048 字符 × utf8mb4 超过 MySQL 3072 字节键长限制，用 768 字符前缀唯一索引。
        op.create_index(
            "uq_listings_url",
            "listings",
            ["listing_url"],
            unique=True,
            mysql_length={"listing_url": 768},
        )
        op.create_index("ix_listings_task", "listings", ["distribution_task_id"])
        op.create_index("ix_listings_operator_user", "listings", ["operator_user_id"])

    # ------------------------------------------------ 2. listing_materials（M2M：素材 ↔ Listing）
    if not inspector.has_table("listing_materials"):
        op.create_table(
            "listing_materials",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("listing_id", mysql.VARCHAR(64), nullable=False),
            # 主素材 = Material（MAT-xxxxxx）
            sa.Column("material_id", mysql.VARCHAR(64), nullable=True),
            # 副素材 = MaterialVariant（1-1 / 2-1）
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["listing_id"], ["listings.id"], name="fk_listing_materials_listing", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["material_id"], ["materials.id"], name="fk_listing_materials_material", ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(
                ["variant_id"],
                ["material_variants.id"],
                name="fk_listing_materials_variant",
                ondelete="CASCADE",
            ),
            comment="素材 ↔ Listing 多对多（material_id=MAT / variant_id=副素材，至少一项）",
            **TABLE_ARGS,
        )
        op.create_index("ix_listing_materials_listing", "listing_materials", ["listing_id"])
        op.create_index("ix_listing_materials_material", "listing_materials", ["material_id"])
        op.create_index("ix_listing_materials_variant", "listing_materials", ["variant_id"])

    # ------------------------------------------------ 3. activity_logs.target_type 扩展 LISTING
    bind.execute(
        sa.text(
            "ALTER TABLE activity_logs DROP CHECK ck_activity_logs_target_type"
        )
    )
    bind.execute(
        sa.text(
            "ALTER TABLE activity_logs ADD CONSTRAINT ck_activity_logs_target_type "
            "CHECK (target_type IN ('DESIGN_PACKAGE','MATERIAL','MATERIAL_VARIANT','DERIVATIVE_BATCH',"
            "'DISTRIBUTION','PARENT_ASIN','CHILD_ASIN','UPLOAD','ASSET','LISTING'))"
        )
    )

    # ------------------------------------------------ 4. 历史数据迁移（幂等可重跑）
    # distribution_parent_asins 存量 → listings（复制不覆盖；URL 空保留 NULL）
    bind.execute(
        sa.text(
            """
            INSERT INTO listings
              (id, distribution_task_id, listing_url, parent_asin, store, site,
               operator_user_id, created_by, created_at, updated_at, parent_asin_bound_at)
            SELECT
              CONCAT('listing-', pa.id),
              pa.distribution_task_id,
              NULLIF(TRIM(pa.listing_url), ''),
              pa.parent_asin,
              NULL,
              pa.site,
              (SELECT t.operator_user_id FROM distribution_tasks t
                 WHERE t.id = pa.distribution_task_id),
              COALESCE((SELECT t.operator_name FROM distribution_tasks t
                 WHERE t.id = pa.distribution_task_id), '系统迁移'),
              pa.created_at, pa.created_at,
              CASE WHEN pa.parent_asin IS NOT NULL AND pa.parent_asin <> '' THEN pa.created_at ELSE NULL END
            FROM distribution_parent_asins pa
            WHERE NOT EXISTS (SELECT 1 FROM listings l WHERE l.id = CONCAT('listing-', pa.id))
            """
        )
    )
    # Listing ↔ Variant（按任务快照 items）
    bind.execute(
        sa.text(
            """
            INSERT INTO listing_materials (id, listing_id, variant_id, created_at)
            SELECT CONCAT('lmigv-', it.id), CONCAT('listing-', pa.id), it.variant_id, pa.created_at
            FROM distribution_parent_asins pa
            JOIN distribution_task_items it ON it.distribution_task_id = pa.distribution_task_id
            WHERE NOT EXISTS (
              SELECT 1 FROM listing_materials lm
              WHERE lm.id = CONCAT('lmigv-', it.id)
            )
            """
        )
    )
    # Listing ↔ MAT（去重：同一 MAT 多个 variant 只挂一条）
    bind.execute(
        sa.text(
            """
            INSERT INTO listing_materials (id, listing_id, material_id, created_at)
            SELECT DISTINCT CONCAT('lmigm-', pa.id, '-', mv.material_id),
                   CONCAT('listing-', pa.id), mv.material_id, pa.created_at
            FROM distribution_parent_asins pa
            JOIN distribution_task_items it ON it.distribution_task_id = pa.distribution_task_id
            JOIN material_variants mv ON mv.id = it.variant_id
            WHERE NOT EXISTS (
              SELECT 1 FROM listing_materials lm
              WHERE lm.id = CONCAT('lmigm-', pa.id, '-', mv.material_id)
            )
            """
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "ALTER TABLE activity_logs DROP CHECK ck_activity_logs_target_type"
        )
    )
    bind.execute(
        sa.text(
            "ALTER TABLE activity_logs ADD CONSTRAINT ck_activity_logs_target_type "
            "CHECK (target_type IN ('DESIGN_PACKAGE','MATERIAL','MATERIAL_VARIANT','DERIVATIVE_BATCH',"
            "'DISTRIBUTION','PARENT_ASIN','CHILD_ASIN','UPLOAD','ASSET'))"
        )
    )
    op.drop_table("listing_materials")
    op.drop_table("listings")
