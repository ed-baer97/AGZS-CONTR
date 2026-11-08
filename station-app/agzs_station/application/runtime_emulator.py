"""Учебный запуск. В промышленный профиль этот модуль не импортируется."""

import secrets

from agzs_station.application.cashier import CashierService
from agzs_station.application.seed import ensure_training_station
from agzs_station.config import Settings
from agzs_station.database.session import make_engine, session_factory
from agzs_station.fiscal.policy import DenyAllFiscalPolicy
from agzs_station.fiscal.training import TrainingNoReceiptPolicy


def open_service(settings: Settings, context: object) -> CashierService:
    engine = make_engine(settings.sqlite_url())
    factory = session_factory(engine)
    with factory() as session:
        ensure_training_station(session, _password(settings))
        session.commit()
    return CashierService(
        factory,
        context.pump,  # type: ignore[attr-defined]
        context.payment,  # type: ignore[attr-defined]
        {
            "DENY_ALL": DenyAllFiscalPolicy(),
            "TRAINING_NO_RECEIPT": TrainingNoReceiptPolicy(),
        },
        env=settings.env,
        database_path=_sqlite_path(settings),
    )


def _password(settings: Settings) -> str:
    if settings.bootstrap_password:
        return settings.bootstrap_password
    path = settings.data_dir / "bootstrap-password.txt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    password = secrets.token_urlsafe(12)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(password + "\n", encoding="utf-8")
    path.chmod(0o600)
    return password


def _sqlite_path(settings: Settings) -> str | None:
    url = settings.sqlite_url()
    prefix = "sqlite:///"
    if url.startswith(prefix):
        return url[len(prefix) :]
    return None
