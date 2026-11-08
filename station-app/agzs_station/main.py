"""Точка входа кассы. На этапе 2 готовит базу и профиль, без окна и без оборудования."""

import logging
import sys

from agzs_station.composition import build
from agzs_station.composition_production import AppContext
from agzs_station.config import Settings
from agzs_station.database.migrate import upgrade_database
from agzs_station.errors import ConfigurationError
from agzs_station.logging_config import configure_logging


def bootstrap(settings: Settings) -> AppContext:
    configure_logging(settings)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    upgrade_database(settings.sqlite_url())
    context = build(settings)
    logging.getLogger("agzs").info("Каркас кассы готов, интерфейс ещё не подключён")
    return context


def main() -> int:
    try:
        bootstrap(Settings())
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
