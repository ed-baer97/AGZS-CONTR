"""column readings

Revision ID: b7c1a2d4e6f0
Revises: 836502097c3b
Create Date: 2026-11-08 16:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c1a2d4e6f0"
down_revision: str | None = "836502097c3b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "links",
        sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "links",
        sa.Column("unavailable", sa.Boolean(), server_default="0", nullable=False),
    )
    op.add_column("pumps", sa.Column("last_totalizer_ml", sa.Integer(), nullable=True))
    op.add_column("pumps", sa.Column("last_dose_ml", sa.Integer(), nullable=True))
    op.add_column("pumps", sa.Column("last_fault_code", sa.String(length=64), nullable=True))
    op.add_column("pumps", sa.Column("last_observed_at", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("pumps", "last_observed_at")
    op.drop_column("pumps", "last_fault_code")
    op.drop_column("pumps", "last_dose_ml")
    op.drop_column("pumps", "last_totalizer_ml")
    op.drop_column("links", "unavailable")
    op.drop_column("links", "consecutive_failures")
