"""add subscriptions.last_reminded_at

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-14 13:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("last_reminded_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("subscriptions", "last_reminded_at")
