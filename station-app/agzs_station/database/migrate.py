from pathlib import Path

from alembic import command
from alembic.config import Config

_INI = Path(__file__).resolve().parents[2] / "alembic.ini"
_SCRIPTS = _INI.parent / "alembic"


def upgrade_database(url: str) -> None:
    command.upgrade(_config(url), "head")


def downgrade_database(url: str, revision: str = "base") -> None:
    command.downgrade(_config(url), revision)


def _config(url: str) -> Config:
    cfg = Config(str(_INI))
    cfg.set_main_option("script_location", str(_SCRIPTS))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg
