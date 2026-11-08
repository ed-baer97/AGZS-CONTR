"""Точка входа кассы. Окно подключается флагом --ui и не говорит с колонкой напрямую."""

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
    logging.getLogger("agzs").info("Касса собрана")
    return context


def open_service(settings: Settings, context: AppContext):
    if settings.env == "production":
        from agzs_station.application.runtime import open_service as open_production

        return open_production(settings, context)
    from agzs_station.application.runtime_emulator import open_service as open_emulator

    return open_emulator(settings, context)


def main() -> int:
    try:
        settings = Settings()
        context = bootstrap(settings)
        service = open_service(settings, context)
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if "--ui" in sys.argv:
        from agzs_station.ui.window import run_window

        return run_window(service, settings.env)
    print("Касса готова. Окно кассира: agzs-station --ui")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
