"""add promo_campaigns and orders.promo_id

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-24 18:50:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "promo_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("trial", "discount", name="promokind"),
            nullable=False,
        ),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column(
            "audience",
            sa.Enum("all", "new", "returning", name="promoaudience"),
            nullable=False,
            server_default="all",
        ),
        sa.Column("grant_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("discount_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("max_uses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invite_link", sa.String(length=512), nullable=True),
        sa.Column("invite_link_name", sa.String(length=64), nullable=True),
        sa.Column("start_payload", sa.String(length=64), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("link_expire_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=True
        ),
    )
    with op.batch_alter_table("orders") as batch_op:
        batch_op.add_column(sa.Column("promo_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("orders") as batch_op:
        batch_op.drop_column("promo_id")
    op.drop_table("promo_campaigns")
    sa.Enum(name="promokind").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="promoaudience").drop(op.get_bind(), checkfirst=True)
