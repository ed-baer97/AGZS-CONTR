"""Схема центра и row-level security для PostgreSQL."""

from alembic import op

from agzs_server.db import Base, enable_row_level_security

revision = "20261108_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind)
    if bind.dialect.name == "postgresql":
        enable_row_level_security(bind.engine)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind)
