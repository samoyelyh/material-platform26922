"""distribution_tasks / items / parent_asins / child_asins（素材域派发 + ASIN）

把前端此前仅存在于内存态（workflowStore.tasks）的「派发运营 → 运营接收 →
回填 Parent / Child ASIN」链路落成真实素材域表，作为对 order-center 只读契约
（Child ASIN → Batch → Variant 候选）的事实来源。订单中心侧不在这里建表。

业务链：DesignPackage → DerivativeBatch → DistributionTask → 运营接收 →
Parent ASIN / Child ASIN。

表设计：
  distribution_tasks           一次派发（batch → operator），带派发快照字段
  distribution_task_items      派发当时的副素材 + Revision 快照（deliveryRound）
  distribution_parent_asins    一个任务一个 Parent ASIN（本身即唯一标识）
  distribution_child_asins     该任务关联的 Child ASIN（订单中心按它检索）
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0011_distribution_asin"
down_revision: str | None = "0010_variant_effect_images"
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

    # ------------------------------------------------ 1. distribution_tasks
    if not sa.inspect(bind).has_table("distribution_tasks"):
        op.create_table(
            "distribution_tasks",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("design_package_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("batch_id", mysql.VARCHAR(64), nullable=False),
            # ---- SNAPSHOT FIELD：派发当时的展示名称，仅供历史文案，不是 join 事实来源 ----
            sa.Column("package_name", mysql.VARCHAR(255), nullable=False),
            sa.Column("package_code", mysql.VARCHAR(64), nullable=False),
            sa.Column("version_code", mysql.VARCHAR(16), nullable=False),
            sa.Column("designer_name", mysql.VARCHAR(128), nullable=False),
            # ---------------------------------------------------------------
            sa.Column("operator_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("operator_name", mysql.VARCHAR(128), nullable=False),
            sa.Column("status", mysql.VARCHAR(16), nullable=False, server_default="ACTIVE"),
            sa.Column("assigned_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("received_at", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("completed_at", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("cancelled_at", mysql.DATETIME(fsp=6), nullable=True),
            sa.Column("remark", sa.Text(), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.Column("updated_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["design_package_id"],
                ["design_packages.id"],
                name="fk_distribution_tasks_pkg",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["batch_id"],
                ["derivative_batches.id"],
                name="fk_distribution_tasks_batch",
                ondelete="CASCADE",
            ),
            sa.CheckConstraint(
                "status IN ('ACTIVE','RECEIVED','COMPLETED','CANCELLED')",
                name="ck_distribution_tasks_status",
            ),
            comment="派发任务（batch → operator）",
            **TABLE_ARGS,
        )
        op.create_index(
            "ix_distribution_tasks_batch_operator",
            "distribution_tasks",
            ["batch_id", "operator_id", "status"],
        )

    # ------------------------------------------------ 2. distribution_task_items
    if not sa.inspect(bind).has_table("distribution_task_items"):
        op.create_table(
            "distribution_task_items",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("distribution_task_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("variant_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("revision_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("delivery_round", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["distribution_task_id"],
                ["distribution_tasks.id"],
                name="fk_distribution_items_task",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["variant_id"],
                ["material_variants.id"],
                name="fk_distribution_items_variant",
                ondelete="CASCADE",
            ),
            sa.ForeignKeyConstraint(
                ["revision_id"],
                ["variant_revisions.id"],
                name="fk_distribution_items_revision",
                ondelete="CASCADE",
            ),
            sa.CheckConstraint("delivery_round >= 1", name="ck_distribution_items_round"),
            comment="派发快照：副素材 + 当时 Revision + 交付轮次",
            **TABLE_ARGS,
        )
        op.create_index(
            "ix_distribution_items_task",
            "distribution_task_items",
            ["distribution_task_id"],
        )
        op.create_index(
            "ix_distribution_items_variant",
            "distribution_task_items",
            ["variant_id"],
        )

    # ------------------------------------------------ 3. distribution_parent_asins
    if not sa.inspect(bind).has_table("distribution_parent_asins"):
        op.create_table(
            "distribution_parent_asins",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("distribution_task_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("parent_asin", mysql.VARCHAR(32), nullable=False),
            sa.Column("site", mysql.VARCHAR(8), nullable=True),
            sa.Column("listing_url", mysql.VARCHAR(2048), nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["distribution_task_id"],
                ["distribution_tasks.id"],
                name="fk_distribution_parent_task",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "distribution_task_id", name="uq_distribution_parent_task"
            ),
            comment="派发任务 Parent ASIN（一个任务一个，本身即唯一标识）",
            **TABLE_ARGS,
        )

    # ------------------------------------------------ 4. distribution_child_asins
    if not sa.inspect(bind).has_table("distribution_child_asins"):
        op.create_table(
            "distribution_child_asins",
            sa.Column("id", mysql.VARCHAR(64), primary_key=True),
            sa.Column("distribution_task_id", mysql.VARCHAR(64), nullable=False),
            sa.Column("child_asin", mysql.VARCHAR(32), nullable=False),
            sa.Column("site", mysql.VARCHAR(8), nullable=True),
            sa.Column("listing_url", mysql.VARCHAR(2048), nullable=True),
            # 预留：Child 单独素材覆盖（本轮不做 UI，仅保留列）
            sa.Column("override_variant_ids", mysql.JSON, nullable=True),
            sa.Column("created_at", mysql.DATETIME(fsp=6), server_default=NOW6, nullable=False),
            sa.ForeignKeyConstraint(
                ["distribution_task_id"],
                ["distribution_tasks.id"],
                name="fk_distribution_child_task",
                ondelete="CASCADE",
            ),
            sa.UniqueConstraint(
                "distribution_task_id", "child_asin", name="uq_distribution_child_task_asin"
            ),
            comment="派发任务 Child ASIN（订单中心契约检索入口）",
            **TABLE_ARGS,
        )
        op.create_index(
            "ix_distribution_child_asin", "distribution_child_asins", ["child_asin"]
        )


def downgrade() -> None:
    op.drop_table("distribution_child_asins")
    op.drop_table("distribution_parent_asins")
    op.drop_table("distribution_task_items")
    op.drop_table("distribution_tasks")
