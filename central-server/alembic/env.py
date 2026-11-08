import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT.parent / "shared"
for path in (ROOT, SHARED):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agzs_server.db import Base, enable_row_level_security  # noqa: E402

config = context.config
target_metadata = Base.metadata
_database_url = os.environ.get("CENTRAL_DATABASE_URL", "").strip()
if _database_url:
    config.set_main_option("sqlalchemy.url", _database_url.replace("%", "%%"))


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        if connection.in_transaction():
            connection.commit()
        if connection.dialect.name == "postgresql":
            enable_row_level_security(connectable)


if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations_online()
