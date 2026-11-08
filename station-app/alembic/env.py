import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

STATION_ROOT = Path(__file__).resolve().parents[1]
SHARED_ROOT = STATION_ROOT.parent / "shared"
for path in (STATION_ROOT, SHARED_ROOT):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

from agzs_station.database import models  # noqa: E402, F401
from agzs_station.database.base import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    configured = config.get_main_option("sqlalchemy.url")
    if not configured:
        raise RuntimeError("Не задан sqlalchemy.url")
    return configured


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        # Иначе SQLAlchemy 2 откатывает соединение и стирает запись alembic_version,
        # хотя DDL SQLite уже зафиксирован.
        if connection.in_transaction():
            connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
