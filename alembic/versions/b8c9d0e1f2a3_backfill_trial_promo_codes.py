"""backfill promo codes for trial campaigns

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-24 20:50:00.000000
"""

from alembic import op
import sqlalchemy as sa
import secrets
import string

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None

_ALPHABET = string.ascii_uppercase + string.digits


def _make_code(length: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def upgrade() -> None:
    conn = op.get_bind()
    used: set[str] = set()
    existing = conn.execute(
        sa.text("SELECT code FROM promo_campaigns WHERE code IS NOT NULL AND code != ''")
    ).fetchall()
    for (c,) in existing:
        used.add(str(c).upper())

    rows = conn.execute(
        sa.text(
            "SELECT id FROM promo_campaigns "
            "WHERE kind = 'trial' AND (code IS NULL OR code = '')"
        )
    ).fetchall()
    for (promo_id,) in rows:
        for _ in range(30):
            candidate = _make_code()
            if candidate not in used:
                break
        else:
            candidate = f"T{promo_id:07d}"[:16]
        used.add(candidate)
        conn.execute(
            sa.text("UPDATE promo_campaigns SET code = :code WHERE id = :id"),
            {"code": candidate, "id": promo_id},
        )


def downgrade() -> None:
    # Keep codes; no-op downgrade for data backfill.
    pass
