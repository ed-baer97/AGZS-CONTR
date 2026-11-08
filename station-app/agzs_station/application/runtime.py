"""Промышленный запуск сценариев. Учебные порты этим модулем не импортируются."""

from agzs_station.application.cashier import CashierService
from agzs_station.config import Settings
from agzs_station.database.session import make_engine, session_factory
from agzs_station.fiscal.policy import DenyAllFiscalPolicy


def open_service(settings: Settings, context: object) -> CashierService:
    engine = make_engine(settings.sqlite_url())
    return CashierService(
        session_factory(engine),
        context.pump,  # type: ignore[attr-defined]
        context.payment,  # type: ignore[attr-defined]
        {"DENY_ALL": DenyAllFiscalPolicy()},
        env=settings.env,
        database_path=_sqlite_path(settings),
    )


def _sqlite_path(settings: Settings) -> str | None:
    url = settings.sqlite_url()
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix) :]
    return None
