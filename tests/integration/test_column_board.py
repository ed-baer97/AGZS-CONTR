"""Управление колонками и съём показаний. Оплата и чек здесь не проверяются."""

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from agzs_shared.enums import (
    AuthorizationStatus,
    DispensingStatus,
    FiscalStatus,
    ObservationQuality,
    OrderStatus,
    PaymentStatus,
    PumpStatus,
    SettlementStatus,
    ShiftStatus,
    SyncStatus,
)
from agzs_shared.ids import new_id
from agzs_station.application.cashier import CashierService
from agzs_station.application.seed import ensure_training_station
from agzs_station.database.journal import verify_chain
from agzs_station.database.migrate import upgrade_database
from agzs_station.database.models import (
    DispensingAuthorization,
    Link,
    Pump,
    PumpEvent,
    Sale,
    Shift,
    StationProfile,
    User,
    UserRole,
)
from agzs_station.database.session import make_engine, session_factory
from agzs_station.errors import AccessDenied, SaleBlocked
from agzs_station.fiscal.training import TrainingNoReceiptPolicy
from agzs_station.payments.fake import FakePaymentPort
from agzs_station.pumps.adapters.emulator import EmulatorAdapter, ManualClock
from agzs_station.pumps.manager import PumpManager
from agzs_station.pumps.supervisor import ColumnSupervisor
from agzs_station.pumps.telemetry import (
    AdapterOutcome,
    AdapterResult,
    LinkStatus,
    PumpTelemetry,
    TotalizerReading,
)
from agzs_station.security.passwords import hash_password


class ScriptPort:
    driver_id = "emulator"
    supports_passive_read = True
    supports_control = False

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_status = 0
        self.link_up = True

    def probe(self) -> LinkStatus:
        self.calls.append("probe")
        return LinkStatus(up=self.link_up)

    def read_status(self, address: str) -> PumpTelemetry:
        self.calls.append(f"status:{address}")
        if self.fail_status:
            self.fail_status -= 1
            raise TimeoutError("timeout")
        return PumpTelemetry(PumpStatus.IDLE, ObservationQuality.CONFIRMED, None, 10, None)

    def read_totalizer(self, address: str) -> TotalizerReading:
        self.calls.append(f"total:{address}")
        return TotalizerReading(10, ObservationQuality.CONFIRMED)

    def authorize(self, address: str, command_id: str, amount_kzt: int, *, price_kzt_per_liter: int | None = None) -> AdapterResult:
        del address, amount_kzt, price_kzt_per_liter
        raise AssertionError("сценарий показаний не отправляет допуск")

    def read_authorization(self, command_id: str) -> AdapterResult:
        return AdapterResult(AdapterOutcome.UNKNOWN, command_id)


def _stand(tmp_path: Path, *, pump_count: int = 2, port: object | None = None):
    url = f"sqlite:///{tmp_path / 'station.db'}"
    upgrade_database(url)
    engine = make_engine(url)
    factory = session_factory(engine)
    clock = ManualClock()
    adapter = port or EmulatorAdapter(
        pump_count=pump_count,
        flow_ml_per_s=1000,
        price_kzt_per_liter=100,
        clock=clock,
    )
    with factory() as session:
        ensure_training_station(session, "training-password", pump_count=pump_count)
        session.commit()
    manager = PumpManager(factory, adapter)
    return manager, ColumnSupervisor(manager), adapter, clock, factory


def _ends(actions: list[str], suffix: str) -> bool:
    return any(action.endswith(suffix) for action in actions)


def test_scheduler_prefers_dispensing_and_waits_for_calm_columns(tmp_path: Path) -> None:
    manager, supervisor, port, _clock, factory = _stand(tmp_path, port=ScriptPort())
    seen: list[str] = []
    for _ in range(6):
        seen.extend(supervisor.acquire(0))
    assert [item.rsplit(":", 1)[-1] for item in seen] == ["1", "2", "1", "2"]
    assert seen[0].endswith(":status:1")
    assert seen[1].endswith(":status:2")
    assert seen[2].endswith(":totalizer:1")
    assert seen[3].endswith(":totalizer:2")
    assert supervisor.acquire(499) == []

    with factory() as session:
        pump = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        pump.status = PumpStatus.DISPENSING
        session.commit()
    assert supervisor.acquire(200)[0].endswith(":status:1")
    assert supervisor.acquire(399) == []
    assert supervisor.acquire(400)[0].endswith(":status:1")
    assert port.calls.count("status:2") == 1


def test_three_timeouts_close_the_link_until_the_recovery_interval(tmp_path: Path) -> None:
    _manager, supervisor, port, _clock, factory = _stand(tmp_path, port=ScriptPort())
    port.fail_status = 3
    assert len(supervisor.acquire(0)) == 1
    assert len(supervisor.acquire(0)) == 1
    assert supervisor.acquire(0)[0].endswith(":status-failed:1")
    assert supervisor.acquire(0) == []
    assert supervisor.acquire(1999) == []
    assert supervisor.acquire(2000)[0].endswith(":probe:up")
    assert port.calls[:4] == ["status:1", "status:2", "status:1", "probe"]
    with factory() as session:
        link = session.scalar(select(Link).limit(1))
        assert link.unavailable is False
        assert link.consecutive_failures == 0
        hidden = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        assert hidden.status == PumpStatus.OFFLINE
        assert hidden.observation_quality == ObservationQuality.UNKNOWN


def test_closed_channel_and_passive_flag_are_not_polled(tmp_path: Path) -> None:
    manager, supervisor, port, _clock, factory = _stand(tmp_path, port=ScriptPort())
    with factory() as session:
        link = session.scalar(select(Link).limit(1))
        link.max_channels = 1
        session.commit()
    seen: list[str] = []
    for _ in range(4):
        seen.extend(supervisor.acquire(0))
    assert seen
    assert all(not action.endswith(":2") for action in seen)
    assert _ends(seen, ":status:1")
    assert _ends(seen, ":totalizer:1")
    with factory() as session:
        skipped = session.scalar(select(Pump).where(Pump.protocol_address == "2"))
        assert skipped.status == PumpStatus.OFFLINE
        assert skipped.observation_quality == ObservationQuality.UNKNOWN

    with factory() as session:
        link = session.scalar(select(Link).limit(1))
        link.passive_read_enabled = False
        link.max_channels = 2
        session.commit()
    before = len(port.calls)
    assert supervisor.acquire(10_000) == []
    assert len(port.calls) == before
    for reading in manager.readings():
        assert reading.totalizer_ml is None
        assert reading.observation_quality == ObservationQuality.UNKNOWN


def test_two_columns_report_their_own_meters(tmp_path: Path) -> None:
    manager, _supervisor, adapter, _clock, factory = _stand(tmp_path)
    with factory() as session:
        pump = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        pump.status = PumpStatus.READY
        pump.observation_quality = ObservationQuality.CONFIRMED
        session.commit()
    adapter.authorize("1", "dose-1", 50, price_kzt_per_liter=100)
    adapter.advance_ms(200)
    manager.poll()
    by_address = {row.address: row for row in manager.readings()}
    assert by_address["1"].status == PumpStatus.DISPENSING
    assert by_address["1"].dose_ml == 200
    assert by_address["1"].totalizer_ml == 200
    assert by_address["2"].status == PumpStatus.IDLE
    assert by_address["2"].dose_ml is None
    assert by_address["2"].totalizer_ml == 0

    adapter.inject_fault("2", "E-VALVE")
    manager.poll()
    by_address = {row.address: row for row in manager.readings()}
    assert by_address["1"].status == PumpStatus.DISPENSING
    assert by_address["2"].status == PumpStatus.ERROR
    assert by_address["2"].fault_code == "E-VALVE"

    adapter.cut_link()
    manager.poll()
    hidden = {row.address: row for row in manager.readings()}
    assert hidden["1"].status == PumpStatus.OFFLINE
    assert hidden["1"].observation_quality == ObservationQuality.UNKNOWN
    assert hidden["1"].dose_ml is None
    assert hidden["1"].totalizer_ml is None
    with factory() as session:
        stored = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        assert stored.last_totalizer_ml == 200
        link = session.get(Link, stored.link_id)
        assert link.unavailable is True
        assert verify_chain(session, "pump_events")


def test_unknown_command_is_not_sent_again_and_a_second_command_is_blocked(tmp_path: Path) -> None:
    manager, _supervisor, adapter, _clock, factory = _stand(tmp_path)
    manager.poll()
    calls = {"count": 0}
    original = adapter.authorize

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    adapter.authorize = counting
    with factory() as session:
        station = session.scalar(select(StationProfile).limit(1))
        user = session.scalar(select(User).limit(1))
        pump = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        pump.status = PumpStatus.READY
        pump.observation_quality = ObservationQuality.CONFIRMED
        shift = Shift(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.station_id,
            user_id=user.id,
            number=1,
            status=ShiftStatus.OPEN,
            opening_cash_kzt=0,
        )
        session.add(shift)
        session.flush()
        sale = Sale(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.station_id,
            shift_id=shift.id,
            pump_id=pump.id,
            display_number="VIRT-1",
            order_status=OrderStatus.OPEN,
            payment_status=PaymentStatus.NONE,
            fiscal_status=FiscalStatus.UNCONFIGURED,
            dispensing_status=DispensingStatus.NOT_STARTED,
            settlement_status=SettlementStatus.NOT_READY,
            sync_status=SyncStatus.NOT_QUEUED,
            prepaid_kzt=50,
            unit_price_kzt_per_liter=100,
        )
        session.add(sale)
        session.flush()
        first = DispensingAuthorization(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.station_id,
            sale_id=sale.id,
            pump_id=pump.id,
            attempt_no=1,
            idempotency_key="command-1",
            status=AuthorizationStatus.CREATED,
            authorized_kzt=50,
        )
        session.add(first)
        session.commit()
        first_id = first.id
        sale_id = sale.id
        pump_id = pump.id
        organization_id = station.organization_id
        station_id = station.station_id

    assert manager.submit_created(first_id) == AuthorizationStatus.ACKNOWLEDGED
    assert calls["count"] == 1
    with factory() as session:
        unknown = DispensingAuthorization(
            id=new_id(),
            organization_id=organization_id,
            station_id=station_id,
            sale_id=sale_id,
            pump_id=pump_id,
            attempt_no=2,
            idempotency_key="command-unknown",
            status=AuthorizationStatus.UNKNOWN,
            authorized_kzt=50,
        )
        second = DispensingAuthorization(
            id=new_id(),
            organization_id=organization_id,
            station_id=station_id,
            sale_id=sale_id,
            pump_id=pump_id,
            attempt_no=3,
            idempotency_key="command-2",
            status=AuthorizationStatus.CREATED,
            authorized_kzt=50,
        )
        session.add(unknown)
        session.add(second)
        session.commit()
        unknown_id = unknown.id
        second_id = second.id
    assert manager.submit_created(unknown_id) == AuthorizationStatus.UNKNOWN
    assert calls["count"] == 1
    with pytest.raises(SaleBlocked, match="команда управления"):
        manager.submit_created(second_id)
    assert calls["count"] == 1
    with factory() as session:
        checkpoint = session.scalar(select(PumpEvent).where(PumpEvent.event_type == "totalizer"))
        body = json.loads(checkpoint.payload_json)
        assert body["phase"] == "authorize"
        assert body["totalizer_ml"] == 0
        assert verify_chain(session, "pump_events")


def test_maintenance_is_a_technician_action_and_does_not_stop_a_live_column(tmp_path: Path) -> None:
    _manager, _supervisor, adapter, _clock, factory = _stand(tmp_path)
    service = CashierService(
        factory,
        adapter,
        FakePaymentPort(),
        {"TRAINING_NO_RECEIPT": TrainingNoReceiptPolicy()},
        env="emulator",
    )
    service.login("cashier", "training-password")
    pump_id = service.pumps()[0].pump_id
    assert service.pumps()[0].totalizer_ml == 0
    with pytest.raises(AccessDenied):
        service.set_maintenance(pump_id, True)
    with factory() as session:
        station = session.scalar(select(StationProfile).limit(1))
        technician = User(
            id=new_id(),
            station_id=station.station_id,
            display_name="tech",
            password_hash=hash_password("tech-pass"),
        )
        session.add(technician)
        session.flush()
        session.add(UserRole(user_id=technician.id, role_code="technician"))
        session.commit()
    service.login("tech", "tech-pass")
    assert service.set_maintenance(pump_id, True) == "Колонка отмечена для обслуживания"
    assert service.pumps()[0].status == PumpStatus.MAINTENANCE
    assert service.set_maintenance(pump_id, False) == "Колонка возвращена из обслуживания"
    assert service.pumps()[0].status == PumpStatus.IDLE

    with factory() as session:
        pump = session.get(Pump, pump_id)
        pump.status = PumpStatus.DISPENSING
        session.commit()
    service.set_maintenance(pump_id, True)
    with factory() as session:
        pump = session.get(Pump, pump_id)
        assert pump.status == PumpStatus.DISPENSING
        assert pump.maintenance_latched is True


def test_illegal_transition_is_rejected_without_changing_the_column(tmp_path: Path) -> None:
    manager, _supervisor, _adapter, _clock, factory = _stand(tmp_path)
    manager.poll()
    with factory() as session:
        pump = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        with pytest.raises(SaleBlocked):
            manager.transition(session, pump, PumpStatus.DISPENSING, reason="мимо схемы")
        session.commit()
        assert session.get(Pump, pump.id).status == PumpStatus.IDLE


def test_column_window_names_the_counter() -> None:
    source = Path(__file__).resolve().parents[2] / "station-app" / "agzs_station" / "ui" / "window.py"
    text = source.read_text(encoding="utf-8")
    assert "счётчик" in text
    assert "доза" in text
