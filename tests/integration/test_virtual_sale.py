from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from agzs_shared.enums import (
    AuthorizationStatus,
    DispensingStatus,
    ObservationQuality,
    OrderStatus,
    PaymentStatus,
    PumpStatus,
    SettlementStatus,
)
from agzs_shared.ids import new_id
from agzs_station.application.cashier import CashierService
from agzs_station.application.seed import ensure_training_station
from agzs_station.database.journal import verify_chain
from agzs_station.database.migrate import upgrade_database
from agzs_station.database.models import (
    DispensingAuthorization,
    PaymentAttempt,
    Pump,
    Refund,
    Sale,
    StationProfile,
)
from agzs_station.database.session import make_engine, session_factory
from agzs_station.errors import AccessDenied, LinkBusy, SaleBlocked
from agzs_station.fiscal.policy import DenyAllFiscalPolicy
from agzs_station.fiscal.training import TrainingNoReceiptPolicy
from agzs_station.payments.fake import FakePaymentPort
from agzs_station.payments.port import PaymentOutcome, PaymentReply
from agzs_station.pumps.adapters.emulator import EmulatorAdapter, ManualClock
from agzs_station.pumps.telemetry import AdapterOutcome, AdapterResult, LinkStatus, PumpTelemetry, TotalizerReading
from agzs_station.pumps.manager import PumpManager
from agzs_station.pumps.sequencer import LinkSequencer


def _service(tmp_path: Path, *, env: str = "emulator", payment=None, port=None, clock=None):
    url = f"sqlite:///{tmp_path / 'station.db'}"
    upgrade_database(url)
    engine = make_engine(url)
    factory = session_factory(engine)
    manual = clock or ManualClock()
    adapter = port or EmulatorAdapter(
        pump_count=2,
        flow_ml_per_s=1000,
        price_kzt_per_liter=100,
        clock=manual,
    )
    with factory() as session:
        ensure_training_station(session, "training-password")
        session.commit()
    service = CashierService(
        factory,
        adapter,
        payment or FakePaymentPort(),
        {"DENY_ALL": DenyAllFiscalPolicy(), "TRAINING_NO_RECEIPT": TrainingNoReceiptPolicy()},
        env=env,
        database_path=str(tmp_path / "station.db"),
        clock=lambda: datetime.now(UTC),
    )
    return service, adapter, manual, factory


def _ready(tmp_path: Path, **kwargs):
    service, adapter, clock, factory = _service(tmp_path, **kwargs)
    service.login("cashier", "training-password")
    service.open_shift()
    return service, adapter, clock, factory, service.pumps()


def test_two_pumps_finish_at_their_prepaid_sums(tmp_path: Path) -> None:
    service, _adapter, clock, factory, pumps = _ready(tmp_path)
    first = service.begin_prepay(pumps[0].pump_id, 10)
    second = service.begin_prepay(pumps[1].pump_id, 25)
    service.take_payment(first.sale_id)
    service.take_payment(second.sale_id)
    service.open_dispense_gate(first.sale_id)
    service.open_dispense_gate(second.sale_id)
    clock.advance_ms(300)
    service.tick()
    with factory() as session:
        rows = {row.prepaid_kzt: row for row in session.scalars(select(Sale)).all()}
        assert rows[10].settlement_status == SettlementStatus.BALANCED
        assert rows[10].actual_kzt == 10
        assert rows[25].actual_kzt == 25
        assert rows[10].order_status == OrderStatus.CLOSED
        assert session.scalar(select(func.count()).select_from(Refund)) == 0
        assert verify_chain(session, "pump_events")
        assert verify_chain(session, "sale_events")


def test_link_loss_hides_volume_until_restore(tmp_path: Path) -> None:
    service, _adapter, clock, _factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 50)
    service.take_payment(sale.sale_id)
    service.open_dispense_gate(sale.sale_id)
    clock.advance_ms(20)
    service.training_cut_link()
    service.tick()
    hidden = service.pumps()[0]
    assert hidden.observation_quality == "UNKNOWN"
    assert hidden.status == PumpStatus.OFFLINE
    clock.advance_ms(600)
    service.training_restore_link()
    service.tick()
    done = service.history()[0]
    assert done.order_status == OrderStatus.CLOSED
    assert done.actual_kzt == 50


def test_repeat_payment_click_does_not_create_a_second_attempt(tmp_path: Path) -> None:
    service, _adapter, _clock, factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.take_payment(sale.sale_id)
    service.take_payment(sale.sale_id)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentAttempt)) == 1


def test_unknown_payment_blocks_a_second_attempt_and_gas(tmp_path: Path) -> None:
    class UnknownPay:
        name = "script"

        def start(self, idempotency_key: str, amount_kzt: int) -> PaymentReply:
            del idempotency_key, amount_kzt
            return PaymentReply(PaymentOutcome.UNKNOWN)

        def query(self, idempotency_key: str) -> PaymentReply:
            del idempotency_key
            return PaymentReply(PaymentOutcome.UNKNOWN)

        def reverse(self, idempotency_key: str) -> PaymentReply:
            del idempotency_key
            return PaymentReply(PaymentOutcome.NOT_SUPPORTED)

    service, adapter, _clock, factory, pumps = _ready(tmp_path, payment=UnknownPay())
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    viewed = service.take_payment(sale.sale_id)
    assert viewed.payment_status == PaymentStatus.UNKNOWN
    service.take_payment(sale.sale_id)
    with pytest.raises(SaleBlocked):
        service.open_dispense_gate(sale.sale_id)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(PaymentAttempt)) == 1
        assert session.scalar(select(func.count()).select_from(DispensingAuthorization)) == 0
    assert adapter.read_status("1").status == "IDLE"


def test_failed_payment_releases_the_pump(tmp_path: Path) -> None:
    class FailedPay:
        name = "script"

        def start(self, idempotency_key: str, amount_kzt: int) -> PaymentReply:
            del idempotency_key, amount_kzt
            return PaymentReply(PaymentOutcome.FAILED)

        def query(self, idempotency_key: str) -> PaymentReply:
            del idempotency_key
            return PaymentReply(PaymentOutcome.FAILED)

        def reverse(self, idempotency_key: str) -> PaymentReply:
            del idempotency_key
            return PaymentReply(PaymentOutcome.NOT_SUPPORTED)

    service, _adapter, _clock, factory, pumps = _ready(tmp_path, payment=FailedPay())
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    viewed = service.take_payment(sale.sale_id)
    assert viewed.order_status == OrderStatus.CANCELLED
    assert service.pumps()[0].status == PumpStatus.IDLE
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(DispensingAuthorization)) == 0


def test_deny_all_does_not_authorize(tmp_path: Path) -> None:
    service, _adapter, _clock, factory, pumps = _ready(tmp_path)
    with factory() as session:
        station = session.scalar(select(StationProfile).limit(1))
        station.fiscal_policy_id = "DENY_ALL"
        session.commit()
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.take_payment(sale.sale_id)
    viewed = service.open_dispense_gate(sale.sale_id)
    assert viewed.dispensing_status == DispensingStatus.NOT_STARTED
    with factory() as session:
        pump = session.get(Pump, pumps[0].pump_id)
        assert pump.status != PumpStatus.READY
        assert session.scalar(select(func.count()).select_from(DispensingAuthorization)) == 0


def test_early_stop_becomes_adjustment_without_refund(tmp_path: Path) -> None:
    service, _adapter, _clock, factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.take_payment(sale.sale_id)
    service.open_dispense_gate(sale.sale_id)
    service.training_stop_early("1")
    service.tick()
    done = service.history()[0]
    assert done.settlement_status == SettlementStatus.ADJUSTMENT_REQUIRED
    assert done.actual_kzt == 0
    assert done.delta_kzt == -10
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 0


def test_latch_does_not_send_a_stop_or_a_new_authorization(tmp_path: Path) -> None:
    service, adapter, clock, _factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 40)
    service.take_payment(sale.sale_id)
    service.open_dispense_gate(sale.sale_id)
    service.latch_commands()
    clock.advance_ms(30)
    service.tick()
    flowing = next(pump for pump in service.pumps() if pump.pump_id == pumps[0].pump_id)
    assert flowing.dose_ml == 30
    other = service.begin_prepay(pumps[1].pump_id, 10)
    service.take_payment(other.sale_id)
    with pytest.raises(SaleBlocked):
        service.open_dispense_gate(other.sale_id)
    assert adapter.read_status("2").status == "IDLE"


def test_restart_does_not_authorize_again(tmp_path: Path) -> None:
    service, adapter, _clock, factory, pumps = _ready(tmp_path)
    calls = {"count": 0}
    original = adapter.authorize

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    adapter.authorize = counting
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.take_payment(sale.sale_id)
    service.open_dispense_gate(sale.sale_id)
    assert calls["count"] == 1
    CashierService(
        factory,
        adapter,
        FakePaymentPort(),
        {"DENY_ALL": DenyAllFiscalPolicy(), "TRAINING_NO_RECEIPT": TrainingNoReceiptPolicy()},
        env="emulator",
    )
    assert calls["count"] == 1
    with factory() as session:
        authorization = session.scalar(select(DispensingAuthorization))
        assert authorization.status == AuthorizationStatus.ACKNOWLEDGED


def test_sent_authorization_is_not_resent_when_the_model_does_not_know_it(tmp_path: Path) -> None:
    service, adapter, _clock, factory, pumps = _ready(tmp_path)
    with factory() as session:
        station = session.scalar(select(StationProfile).limit(1))
        sale = service.begin_prepay(pumps[0].pump_id, 10)
        session.add(
            DispensingAuthorization(
                id=new_id(),
                organization_id=station.organization_id,
                station_id=station.station_id,
                sale_id=sale.sale_id,
                pump_id=pumps[0].pump_id,
                attempt_no=1,
                idempotency_key="missing-command",
                status=AuthorizationStatus.SENT,
                authorized_kzt=10,
            )
        )
        session.commit()
    calls = {"count": 0}
    original = adapter.authorize

    def counting(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    adapter.authorize = counting
    CashierService(
        factory,
        adapter,
        FakePaymentPort(),
        {"TRAINING_NO_RECEIPT": TrainingNoReceiptPolicy()},
        env="emulator",
    )
    assert calls["count"] == 0
    with factory() as session:
        row = session.scalar(
            select(DispensingAuthorization).where(DispensingAuthorization.idempotency_key == "missing-command")
        )
        assert row.status == AuthorizationStatus.UNKNOWN


def test_illegal_transition_is_rejected(tmp_path: Path) -> None:
    _service_obj, adapter, _clock, factory, _pumps = _ready(tmp_path)
    manager = PumpManager(factory, adapter)
    with factory() as session:
        pump = session.scalar(select(Pump).where(Pump.protocol_address == "1"))
        with pytest.raises(SaleBlocked):
            manager.transition(session, pump, PumpStatus.DISPENSING, reason="мимо схемы")
        session.commit()
        assert session.get(Pump, pump.id).status == PumpStatus.IDLE


def test_nested_exchange_is_rejected() -> None:
    sequencer = LinkSequencer()

    def inner() -> None:
        sequencer.exchange(lambda: None)

    with pytest.raises(LinkBusy):
        sequencer.exchange(inner)


def test_production_profile_does_not_open_a_sale(tmp_path: Path) -> None:
    service, _adapter, _clock, factory, pumps = _ready(tmp_path, env="production")
    with factory() as session:
        station = session.scalar(select(StationProfile).limit(1))
        station.rounding_policy_id = None
        station.fiscal_policy_id = None
        session.commit()
    with pytest.raises(SaleBlocked):
        service.begin_prepay(pumps[0].pump_id, 10)


def test_login_locks_after_repeated_failures(tmp_path: Path) -> None:
    service, _adapter, _clock, _factory = _service(tmp_path)
    now = {"at": datetime.now(UTC)}
    service._clock = lambda: now["at"]
    for _ in range(5):
        with pytest.raises(AccessDenied):
            service.login("cashier", "wrong")
    with pytest.raises(AccessDenied, match="заблокирован"):
        service.login("cashier", "training-password")
    now["at"] = now["at"] + timedelta(minutes=16)
    service.login("cashier", "training-password")


def test_over_delivery_is_an_incident_without_a_refund(tmp_path: Path) -> None:
    class OverPump:
        driver_id = "emulator"
        supports_control = True
        supports_passive_read = True

        def __init__(self) -> None:
            self.started = False

        def probe(self) -> LinkStatus:
            return LinkStatus(up=True)

        def read_status(self, address: str) -> PumpTelemetry:
            del address
            volume = 5000 if self.started else 0
            status = PumpStatus.COMPLETED if self.started else PumpStatus.IDLE
            return PumpTelemetry(status, ObservationQuality.CONFIRMED, volume if self.started else None, volume, None)

        def read_totalizer(self, address: str) -> TotalizerReading:
            del address
            return TotalizerReading(5000 if self.started else 0, ObservationQuality.CONFIRMED)

        def authorize(self, address: str, command_id: str, amount_kzt: int, *, price_kzt_per_liter: int | None = None) -> AdapterResult:
            del address, amount_kzt, price_kzt_per_liter
            self.started = True
            return AdapterResult(AdapterOutcome.ACKNOWLEDGED, command_id, 0)

        def read_authorization(self, command_id: str) -> AdapterResult:
            if not self.started:
                return AdapterResult(AdapterOutcome.UNKNOWN, command_id)
            return AdapterResult(AdapterOutcome.ACKNOWLEDGED, command_id, 0)

        def release(self, address: str) -> None:
            del address

    service, _adapter, _clock, factory, pumps = _ready(tmp_path, port=OverPump())
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.take_payment(sale.sale_id)
    service.open_dispense_gate(sale.sale_id)
    service.tick()
    done = service.history()[0]
    assert done.actual_kzt == 500
    assert done.delta_kzt == 490
    assert done.settlement_status == SettlementStatus.NOT_READY
    assert service.pumps()[0].status == PumpStatus.ERROR
    assert any(item.code == "OVER_DELIVERY" for item in service.incidents())
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(Refund)) == 0


def test_config_changes_the_next_price_and_rejects_pump_commands(tmp_path: Path) -> None:
    service, _adapter, _clock, factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.apply_config(
        {
            "revision": 1,
            "sales_blocked": False,
            "pumps": [{"pump_id": pumps[0].pump_id, "price_kzt_per_liter": 120}],
        }
    )
    with factory() as session:
        assert session.get(Sale, sale.sale_id).unit_price_kzt_per_liter == 100
        assert session.get(Pump, pumps[0].pump_id).price_kzt_per_liter == 120
    with pytest.raises(SaleBlocked):
        service.apply_config({"revision": 2, "authorize": True})


def test_sqlite_backup_keeps_the_sale(tmp_path: Path) -> None:
    service, _adapter, _clock, _factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    path = service.backup()
    engine = make_engine(f"sqlite:///{path}")
    with session_factory(engine)() as session:
        stored = session.get(Sale, sale.sale_id)
        assert stored.prepaid_kzt == 10


def test_ui_package_does_not_import_adapters_or_sql() -> None:
    root = Path(__file__).resolve().parents[2] / "station-app" / "agzs_station" / "ui"
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "sqlalchemy" not in text
        assert "agzs_station.pumps" not in text
        assert "adapters" not in text
