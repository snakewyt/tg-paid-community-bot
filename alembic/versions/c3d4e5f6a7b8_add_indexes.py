"""Add query indexes and partial unique index for active subscriptions."""

from alembic import op

revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_subscriptions_status_expires", "subscriptions", ["status", "expires_at"])
    op.create_index("ix_subscriptions_user_group", "subscriptions", ["user_id", "group_chat_id"])
    op.create_index("ix_orders_status_created", "orders", ["status", "created_at"])
    op.create_index("ix_orders_user_plan_status", "orders", ["user_id", "plan_id", "status"])
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sub_active_user_group
        ON subscriptions (user_id, group_chat_id)
        WHERE status = 'active'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_sub_active_user_group")
    op.drop_index("ix_orders_user_plan_status", table_name="orders")
    op.drop_index("ix_orders_status_created", table_name="orders")
    op.drop_index("ix_subscriptions_user_group", table_name="subscriptions")
    op.drop_index("ix_subscriptions_status_expires", table_name="subscriptions")
