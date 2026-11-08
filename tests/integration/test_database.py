from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from agzs_shared.enums import (
    DispensingStatus,
    FiscalStatus,
    OrderStatus,
    PaymentStatus,
    SettlementStatus,
    ShiftStatus,
    SyncStatus,
)
from agzs_shared.ids import new_id
from agzs_station.config import Settings
from agzs_station.database.migrate import downgrade_database, upgrade_database
from agzs_station.database.models import Link, Pump, Shift, StationProfile, User
from agzs_station.database.session import make_engine, session_factory
from agzs_station.main import bootstrap


def test_migration_enforces_keys_and_stores_tenge(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'station.db'}"
    upgrade_database(url)
    engine = make_engine(url)
    with engine.connect() as connection:
        assert connection.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert str(connection.execute(text("PRAGMA journal_mode")).scalar()).lower() == "wal"

    columns = {column["name"] for column in inspect(engine).get_columns("pumps")}
    assert "price_kzt_per_liter" in columns
    assert "price_tiyn_per_liter" not in columns

    factory = session_factory(engine)
    with factory() as session:
        station = StationProfile(
            station_id=new_id(),
            organization_id=new_id(),
            code="A1",
            name="Тестовая АГЗС",
            timezone="Asia/Almaty",
        )
        session.add(station)
        session.flush()
        link = Link(
            id=new_id(),
            station_id=station.station_id,
            driver="emulator",
            name="Эмулятор",
            max_channels=2,
            control_enabled=True,
        )
        session.add(link)
        session.flush()
        pump = Pump(
            id=new_id(),
            link_id=link.id,
            channel_no=1,
            protocol_address="1",
            name="Колонка 1",
            price_kzt_per_liter=95,
        )
        session.add(pump)
        session.commit()
        assert session.get(Pump, pump.id).price_kzt_per_liter == 95

    with factory() as session:
        session.add(
            Pump(
                id=new_id(),
                link_id=new_id(),
                channel_no=1,
                protocol_address="x",
                name="Чужая",
                price_kzt_per_liter=10,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with factory() as session:
        station_id = session.execute(text("SELECT station_id FROM station_profile")).scalar_one()
        link_id = session.execute(text("SELECT id FROM links")).scalar_one()
        session.add(
            Pump(
                id=new_id(),
                link_id=link_id,
                channel_no=2,
                protocol_address="2",
                name="Дорогая",
                price_kzt_per_liter=-5,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with factory() as session:
        session.add(
            Link(
                id=new_id(),
                station_id=station_id,
                driver="aska02",
                name="АСКА",
                max_channels=0,
                control_enabled=True,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with factory() as session:
        user = User(
            id=new_id(),
            station_id=station_id,
            display_name="Кассир",
            password_hash="hash-only",
        )
        session.add(user)
        session.flush()
        session.add(
            Shift(
                id=new_id(),
                organization_id=new_id(),
                station_id=station_id,
                user_id=user.id,
                number=1,
                status=ShiftStatus.OPEN.value,
                opening_cash_kzt=0,
            )
        )
        session.commit()
        session.add(
            Shift(
                id=new_id(),
                organization_id=new_id(),
                station_id=station_id,
                user_id=user.id,
                number=2,
                status=ShiftStatus.OPEN.value,
                opening_cash_kzt=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    engine.dispose()
    downgrade_database(url)
    fresh = make_engine(url)
    with fresh.connect() as connection:
        tables = set(inspect(connection).get_table_names())
    assert "pumps" not in tables
    fresh.dispose()
    upgrade_database(url)
    restored = make_engine(url)
    with restored.connect() as connection:
        assert "sales" in inspect(connection).get_table_names()
    restored.dispose()


def test_bootstrap_upgrades_empty_database(tmp_path: Path) -> None:
    data = tmp_path / "data"
    logs = tmp_path / "logs"
    settings = Settings(
        env="production",
        pump_driver="aska02",
        data_dir=data,
        log_dir=logs,
        log_level="INFO",
        central_base_url="",
        database_url=f"sqlite:///{data / 'station.db'}",
        keyring_service_name="agzs-control",
    )
    context = bootstrap(settings)
    assert context.driver_id == "aska02"
    assert context.payment_port_name == "kaspi_smart_pos"
    assert context.fiscal_policy_id == "DENY_ALL"
    assert context.pump_control_enabled is False
    assert (data / "station.db").exists()


def test_sale_status_tokens_match_shared_contract() -> None:
    assert OrderStatus.DRAFT.value == "DRAFT"
    assert PaymentStatus.CONFIRMED.value == "CONFIRMED"
    assert FiscalStatus.UNCONFIGURED.value == "UNCONFIGURED"
    assert DispensingStatus.NOT_STARTED.value == "NOT_STARTED"
    assert SettlementStatus.NOT_READY.value == "NOT_READY"
    assert SyncStatus.NOT_QUEUED.value == "NOT_QUEUED"
