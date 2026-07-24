"""add promo_campaigns.code for in-bot redemption

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-24 20:35:00.000000
"""

from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("promo_campaigns") as batch_op:
        batch_op.add_column(sa.Column("code", sa.String(length=16), nullable=True))
        batch_op.create_unique_constraint("uq_promo_campaigns_code", ["code"])

    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT id, start_payload FROM promo_campaigns "
            "WHERE kind = 'discount' AND (code IS NULL OR code = '')"
        )
    ).fetchall()
    used: set[str] = set()
    existing = conn.execute(
        sa.text("SELECT code FROM promo_campaigns WHERE code IS NOT NULL AND code != ''")
    ).fetchall()
    for (c,) in existing:
        used.add(str(c).upper())

    for promo_id, start_payload in rows:
        raw = (start_payload or "").strip()
        if raw.lower().startswith("promo_"):
            candidate = raw[6:].upper()
        else:
            candidate = f"P{promo_id:04d}"
        # Keep within 4–16 A-Z0-9
        candidate = "".join(ch for ch in candidate if ch.isalnum())[:16].upper()
        if len(candidate) < 4:
            candidate = f"P{promo_id:04d}"
        base = candidate
        n = 0
        while candidate in used:
            n += 1
            suffix = str(n)
            candidate = (base[: 16 - len(suffix)] + suffix).upper()
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE promo_campaigns SET code = :code WHERE id = :id"),
            {"code": candidate, "id": promo_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("promo_campaigns") as batch_op:
        batch_op.drop_constraint("uq_promo_campaigns_code", type_="unique")
        batch_op.drop_column("code")
