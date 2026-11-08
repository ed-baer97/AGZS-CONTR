"""Сценарии кассы. Интерфейс вызывает их и не пишет в базу сам.

Промышленный профиль не получает учебную оплату и учебный чек: округление
и фискальный шлюз остаются закрытыми, пока нет утверждённого правила.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agzs_shared.enums import (
    AuthorizationStatus,
    DispenseEndReason,
    DispensingStatus,
    FiscalDocumentStatus,
    FiscalStatus,
    IncidentSeverity,
    ObservationQuality,
    OrderStatus,
    PaymentAttemptStatus,
    PaymentStatus,
    PumpStatus,
    SettlementStatus,
    ShiftStatus,
    SyncEventType,
    SyncStatus,
)
from agzs_shared.ids import new_id
from agzs_shared.money import parse_tenge
from agzs_station.application.views import IncidentView, PumpView, SaleView, ShiftView
from agzs_station.database.backup import backup_sqlite
from agzs_station.database.journal import (
    append_audit,
    append_sale_event,
    enqueue,
    verify_chain,
)
from agzs_station.database.models import (
    DispensingAuthorization,
    DispensingResult,
    FiscalDocument,
    Incident,
    Link,
    PaymentAttempt,
    Pump,
    Refund,
    Sale,
    Setting,
    Shift,
    StationProfile,
    User,
)
from agzs_station.database.time import utc_now
from agzs_station.domain.settlement import TEST_HALF_UP, actual_kzt, classify
from agzs_station.errors import (
    AccessDenied,
    ConfigurationError,
    IntegrationNotConfigured,
    SaleBlocked,
)
from agzs_station.fiscal.policy import DenyAllFiscalPolicy, GateDecision
from agzs_station.fiscal.webkassa import WebkassaPort
from agzs_station.payments.port import PaymentOutcome, PaymentReply
from agzs_station.pumps.manager import PumpManager
from agzs_station.pumps.telemetry import AdapterOutcome
from agzs_station.security.access import (
    PRICE_CHANGE,
    PUMP_MAINTAIN,
    SALE_CREATE,
    SHIFT_MANAGE,
    STATION_SETTINGS,
    permissions_of,
    require,
)
from agzs_station.security.passwords import verify_password

_LOCK_AFTER = 5
_LOCK_MINUTES = 15


class CashierService:
    def __init__(
        self,
        factory: sessionmaker[Session],
        pump_port: object,
        payment_port: object,
        policies: dict[str, object],
        *,
        env: str,
        database_path: str | None = None,
        fiscal_port: object | None = None,
        clock: Callable[[], datetime] | None = None,
        reserve_timeout_s: int = 120,
    ) -> None:
        self._factory = factory
        self._port = pump_port
        self._payment = payment_port
        self._policies = policies
        self.env = env
        self._database_path = database_path
        self._fiscal = fiscal_port or WebkassaPort()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._reserve_timeout_s = reserve_timeout_s
        self._manager = PumpManager(factory, pump_port)
        self.actor_id: str | None = None
        self._failures: dict[str, tuple[int, datetime | None]] = {}
        self.training = env != "production" and hasattr(pump_port, "cut_link")
        self._manager.recover()
        self._manager.poll()

    def login(self, display_name: str, password: str) -> None:
        now = self._clock()
        locked = self._failures.get(display_name)
        if locked is not None and locked[1] is not None and now < locked[1]:
            raise AccessDenied("Вход временно заблокирован")
        with self._factory() as session:
            station = _station(session)
            user = session.scalar(
                select(User).where(
                    User.station_id == station.station_id,
                    User.display_name == display_name,
                    User.is_active.is_(True),
                )
            )
            ok = user is not None and verify_password(user.password_hash, password)
            append_audit(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                actor_user_id=None if user is None else user.id,
                action="login",
                object_type="user",
                object_id=None if user is None else user.id,
                result="ok" if ok else "denied",
            )
            session.commit()
        if not ok or user is None:
            count, until = self._failures.get(display_name, (0, None))
            count += 1
            until = now + timedelta(minutes=_LOCK_MINUTES) if count >= _LOCK_AFTER else None
            self._failures[display_name] = (count, until)
            raise AccessDenied("Неверное имя или пароль")
        self._failures.pop(display_name, None)
        self.actor_id = user.id

    def open_shift(self) -> ShiftView:
        with self._factory() as session:
            station = _station(session)
            require(session, self.actor_id, SHIFT_MANAGE)
            if not verify_chain(session, "pump_events") or not verify_chain(session, "sale_events"):
                _incident(
                    session,
                    station,
                    IncidentSeverity.CRITICAL,
                    "CHAIN_BROKEN",
                    "Цепочка журнала повреждена и не пересчитывается",
                )
            opened = session.scalar(select(Shift).where(Shift.status == ShiftStatus.OPEN))
            if opened is not None:
                raise SaleBlocked("Смена уже открыта")
            from agzs_station.database.journal import next_number

            shift = Shift(
                id=new_id(),
                organization_id=station.organization_id,
                station_id=station.station_id,
                user_id=self.actor_id or "",
                number=next_number(session, "shift"),
                status=ShiftStatus.OPEN,
                opened_at=utc_now(),
                opening_cash_kzt=0,
            )
            session.add(shift)
            session.flush()
            enqueue(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                event_type=SyncEventType.SHIFT_OPENED,
                payload={"shift_id": shift.id, "number": shift.number, "user_id": shift.user_id},
            )
            session.commit()
            return ShiftView(shift.id, shift.number, shift.status)

    def close_shift(self) -> ShiftView:
        with self._factory() as session:
            station = _station(session)
            require(session, self.actor_id, SHIFT_MANAGE)
            shift = _open_shift(session)
            busy_payment = session.scalar(
                select(Sale).where(Sale.payment_status.in_([PaymentStatus.PENDING, PaymentStatus.UNKNOWN]))
            )
            busy_gas = session.scalar(
                select(Sale).where(
                    Sale.dispensing_status.in_(
                        [
                            DispensingStatus.AUTHORIZED,
                            DispensingStatus.IN_PROGRESS,
                            DispensingStatus.UNKNOWN,
                        ]
                    )
                )
            )
            busy_pump = session.scalar(
                select(Pump).where(Pump.status.in_([PumpStatus.DISPENSING, PumpStatus.READY]))
            )
            if busy_payment is not None or busy_gas is not None or busy_pump is not None:
                raise SaleBlocked("Смену нельзя закрыть, пока есть незавершённая оплата или отпуск")
            shift.status = ShiftStatus.CLOSED
            shift.closed_at = utc_now()
            shift.closing_cash_kzt = 0
            enqueue(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                event_type=SyncEventType.SHIFT_CLOSED,
                payload={"shift_id": shift.id, "number": shift.number},
            )
            session.commit()
            return ShiftView(shift.id, shift.number, shift.status)

    def pumps(self) -> list[PumpView]:
        if self.actor_id is None:
            raise AccessDenied("Нужен вход")
        with self._factory() as session:
            rows = session.scalars(select(Pump).order_by(Pump.channel_no)).all()
            views = [self._pump_view(session, pump) for pump in rows]
            session.rollback()
            return views

    def begin_prepay(self, pump_id: str, amount_kzt: int) -> SaleView:
        amount = parse_tenge(amount_kzt)
        if amount <= 0:
            raise SaleBlocked("Сумма предоплаты должна быть больше нуля")
        with self._factory() as session:
            station = _station(session)
            require(session, self.actor_id, SALE_CREATE)
            if station.sales_blocked:
                raise SaleBlocked("Новые продажи на станции заблокированы")
            if self.env == "production" or station.rounding_policy_id != TEST_HALF_UP:
                raise SaleBlocked("Политика округления не задана или не утверждена")
            shift = _open_shift(session)
            pump = session.get(Pump, pump_id)
            if pump is None:
                raise SaleBlocked("Колонка не найдена")
            if pump.status != PumpStatus.IDLE or pump.observation_quality != ObservationQuality.CONFIRMED:
                raise SaleBlocked("Колонка не свободна или её состояние не подтверждено")
            if pump.active_sale_id is not None:
                raise SaleBlocked("На колонке уже есть продажа")
            local_day = self._clock().astimezone(ZoneInfo(station.timezone))
            from agzs_station.database.journal import next_number

            sale = Sale(
                id=new_id(),
                organization_id=station.organization_id,
                station_id=station.station_id,
                shift_id=shift.id,
                pump_id=pump.id,
                display_number=f"{station.code}-{local_day.strftime('%Y%m%d')}-{next_number(session, 'sale'):06d}",
                order_status=OrderStatus.DRAFT,
                payment_status=PaymentStatus.NONE,
                fiscal_status=FiscalStatus.UNCONFIGURED,
                dispensing_status=DispensingStatus.NOT_STARTED,
                settlement_status=SettlementStatus.NOT_READY,
                sync_status=SyncStatus.PENDING,
                prepaid_kzt=amount,
                unit_price_kzt_per_liter=pump.price_kzt_per_liter,
                rounding_policy_id=station.rounding_policy_id,
                currency="KZT",
            )
            session.add(sale)
            session.flush()
            pump.active_sale_id = sale.id
            self._manager.transition(session, pump, PumpStatus.RESERVED, reason="Кассир начал продажу")
            append_sale_event(
                session,
                sale_id=sale.id,
                axis="order",
                from_value=None,
                to_value=OrderStatus.DRAFT,
                reason="Создана предоплата",
            )
            _queue_sale(session, station, sale, SyncEventType.SALE_CREATED)
            session.commit()
            return _view(sale, "Продажа создана, оплата ещё не отправлена")

    def take_payment(self, sale_id: str) -> SaleView:
        with self._factory() as session:
            station = _station(session)
            require(session, self.actor_id, SALE_CREATE)
            sale = _sale(session, sale_id)
            latest = _latest_attempt(session, sale.id)
            if sale.payment_status == PaymentStatus.CONFIRMED:
                return _view(sale, "Оплата уже подтверждена")
            if latest is not None and latest.status in {
                PaymentAttemptStatus.SENT,
                PaymentAttemptStatus.UNKNOWN,
            }:
                attempt = latest
                action = "query"
            elif latest is not None and latest.status == PaymentAttemptStatus.CREATED:
                attempt = latest
                action = "start"
            elif latest is not None and latest.status == PaymentAttemptStatus.FAILED:
                attempt = _new_attempt(session, station, sale, latest.attempt_no + 1)
                action = "start"
            elif latest is None:
                attempt = _new_attempt(session, station, sale, 1)
                action = "start"
            else:
                raise SaleBlocked("Повторная оплата этой продажи не разрешена")
            key = attempt.idempotency_key
            amount = attempt.amount_kzt
            attempt.provider = getattr(self._payment, "name", "unknown")
            pump = session.get(Pump, sale.pump_id)
            if action == "start" and latest is not None and latest.status == PaymentAttemptStatus.FAILED:
                if pump is None or (pump.active_sale_id not in {None, sale.id}) or pump.status != PumpStatus.IDLE:
                    raise SaleBlocked("Колонка уже занята, повторить оплату нельзя")
                pump.active_sale_id = sale.id
                self._manager.transition(session, pump, PumpStatus.RESERVED, reason="Повторная попытка оплаты")
                if sale.order_status == OrderStatus.CANCELLED:
                    _axis(session, sale, "order", OrderStatus.DRAFT, "Повторная попытка оплаты")
            session.commit()
        try:
            if action == "query":
                reply = self._payment.query(key)  # type: ignore[attr-defined]
            else:
                reply = self._payment.start(key, amount)  # type: ignore[attr-defined]
        except IntegrationNotConfigured as exc:
            return self._reload(sale_id, str(exc))
        return self._apply_payment(sale_id, key, reply)

    def open_dispense_gate(self, sale_id: str) -> SaleView:
        with self._factory() as session:
            station = _station(session)
            require(session, self.actor_id, SALE_CREATE)
            if _latched(session):
                raise SaleBlocked("Новые команды запрещены. Газ останавливается аппаратной кнопкой")
            sale = _sale(session, sale_id)
            pump = session.get(Pump, sale.pump_id)
            link = session.get(Link, pump.link_id) if pump is not None else None
            refusal = _gate_refusal(sale, pump, link, self._port)
            if refusal is not None:
                raise SaleBlocked(refusal)
            existing = session.scalar(
                select(DispensingAuthorization).where(
                    DispensingAuthorization.sale_id == sale.id,
                    DispensingAuthorization.status.in_(
                        [
                            AuthorizationStatus.CREATED,
                            AuthorizationStatus.SENT,
                            AuthorizationStatus.ACKNOWLEDGED,
                            AuthorizationStatus.UNKNOWN,
                        ]
                    ),
                )
            )
            if existing is not None:
                raise SaleBlocked("По продаже уже есть допуск, новый пуск запрещён")
            policy = self._policy(station.fiscal_policy_id)
            decision = policy.evaluate(sale)
            if not decision.allowed:
                append_sale_event(
                    session,
                    sale_id=sale.id,
                    axis="fiscal",
                    from_value=sale.fiscal_status,
                    to_value=sale.fiscal_status,
                    reason=decision.reason,
                )
                session.commit()
                return _view(sale, decision.reason)
            steps = policy.steps_for(sale, "before_dispense")
            if steps:
                document = _fiscal_document(session, station, sale, steps[0])
                document_key = document.idempotency_key
                step_code = document.step_code
                session.commit()
            else:
                document = None
                document_key = ""
                step_code = ""
                self._mark_training_fiscal(session, sale, policy, decision)
                ready = self._arm(session, station, sale, pump)
                session.commit()
                authorization_id = ready
        if document is not None:
            try:
                self._fiscal.submit(step_code, document_key)  # type: ignore[attr-defined]
            except IntegrationNotConfigured as exc:
                return self._reload(sale_id, str(exc))
            return self._reload(sale_id, "Фискальный ответ не разобран: контракт Webkassa не утверждён")
        outcome = self._manager.submit_created(authorization_id)
        return self._after_authorization(sale_id, outcome)

    def tick(self) -> None:
        self._expire_reservations()
        self._manager.poll()
        with self._factory() as session:
            sales = session.scalars(
                select(Sale).where(
                    Sale.dispensing_status.in_(
                        [DispensingStatus.AUTHORIZED, DispensingStatus.IN_PROGRESS]
                    )
                )
            ).all()
            for sale in sales:
                pump = session.get(Pump, sale.pump_id)
                if pump is None:
                    continue
                if pump.status == PumpStatus.DISPENSING and sale.dispensing_status == DispensingStatus.AUTHORIZED:
                    _axis(session, sale, "dispensing", DispensingStatus.IN_PROGRESS, "Идёт отпуск")
                if pump.status in {PumpStatus.COMPLETED, PumpStatus.STOPPED} and sale.actual_kzt is None:
                    self._finish(session, sale, pump)
                fault = self._manager.telemetry.get(pump.id)
                if fault is not None and fault.fault_code and pump.status == PumpStatus.ERROR:
                    _incident(
                        session,
                        _station(session),
                        IncidentSeverity.CRITICAL,
                        fault.fault_code,
                        "Авария колонки",
                        pump_id=pump.id,
                        sale_id=sale.id,
                        once=True,
                    )
            session.commit()

    def latch_commands(self) -> str:
        with self._factory() as session:
            require(session, self.actor_id, SALE_CREATE)
            row = session.get(Setting, "command_latch")
            if row is None:
                session.add(Setting(key="command_latch", value_json="true"))
            else:
                row.value_json = "true"
            station = _station(session)
            append_audit(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                actor_user_id=self.actor_id,
                action="command_latch",
                object_type="station",
                object_id=station.station_id,
                result="latched",
            )
            session.commit()
        return "Новые команды запрещены. Для останова газа используйте аппаратную кнопку"

    def set_price(self, pump_id: str, price_kzt_per_liter: int) -> None:
        price = parse_tenge(price_kzt_per_liter)
        with self._factory() as session:
            require(session, self.actor_id, PRICE_CHANGE)
            pump = session.get(Pump, pump_id)
            if pump is None:
                raise SaleBlocked("Колонка не найдена")
            pump.price_kzt_per_liter = price
            station = _station(session)
            append_audit(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                actor_user_id=self.actor_id,
                action="price_change",
                object_type="pump",
                object_id=pump.id,
                result="ok",
                details={"price_kzt_per_liter": price},
            )
            session.commit()

    def set_sales_blocked(self, blocked: bool) -> None:
        with self._factory() as session:
            require(session, self.actor_id, STATION_SETTINGS)
            station = _station(session)
            station.sales_blocked = blocked
            append_audit(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                actor_user_id=self.actor_id,
                action="sales_blocked",
                object_type="station",
                object_id=station.station_id,
                result="blocked" if blocked else "open",
            )
            session.commit()

    def set_maintenance(self, pump_id: str, enabled: bool) -> str:
        with self._factory() as session:
            station = _station(session)
            require(session, self.actor_id, PUMP_MAINTAIN)
            pump = session.get(Pump, pump_id)
            if pump is None:
                raise SaleBlocked("Колонка не найдена")
            self._manager.set_maintenance(session, pump, enabled)
            append_audit(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                actor_user_id=self.actor_id,
                action="pump.maintenance",
                object_type="pump",
                object_id=pump.id,
                result="latched" if enabled else "released",
            )
            session.commit()
            if enabled and pump.status == PumpStatus.MAINTENANCE:
                return "Колонка отмечена для обслуживания"
            if enabled:
                return "Обслуживание отмечено и начнётся, когда переход станет допустим"
            if pump.status == PumpStatus.IDLE:
                return "Колонка возвращена из обслуживания"
            return "Признак обслуживания снят"

    def history(self) -> list[SaleView]:
        with self._factory() as session:
            owned = permissions_of(session, self.actor_id or "")
            if SALE_CREATE not in owned and "report.read" not in owned:
                raise AccessDenied("Недостаточно прав")
            rows = session.scalars(select(Sale).order_by(Sale.created_at.desc()).limit(50)).all()
            return [_view(row, "") for row in rows]

    def incidents(self) -> list[IncidentView]:
        with self._factory() as session:
            rows = session.scalars(select(Incident).order_by(Incident.occurred_at.desc()).limit(50)).all()
            return [
                IncidentView(row.id, row.severity, row.code, row.message, row.pump_id, row.sale_id)
                for row in rows
            ]

    def permissions(self) -> set[str]:
        if self.actor_id is None:
            return set()
        with self._factory() as session:
            return permissions_of(session, self.actor_id)

    def backup(self) -> str:
        if not self._database_path:
            raise SaleBlocked("Путь базы не задан")
        stamp = self._clock().strftime("%Y%m%dT%H%M%SZ")
        destination = self._database_path.replace("station.db", f"backups/station-{stamp}.db")
        from pathlib import Path

        source = Path(self._database_path)
        target = source.parent / "backups" / f"station-{stamp}.db"
        backup_sqlite(source, target)
        return str(target) if destination else str(target)

    def training_cut_link(self) -> None:
        self._training("cut_link")

    def training_restore_link(self) -> None:
        self._training("restore_link")

    def training_set_flow(self, address: str, flow_ml_per_s: int) -> None:
        self._training("set_flow_ml_per_s", address, flow_ml_per_s)

    def training_fault(self, address: str, code: str) -> None:
        self._training("inject_fault", address, code)

    def training_clear_fault(self, address: str) -> None:
        self._training("clear_fault", address)

    def training_stop_early(self, address: str) -> None:
        self._training("stop_early", address)

    def apply_config(self, document: dict[str, object]) -> None:
        forbidden = {"authorize", "start", "stop", "dispense", "dose"}
        if forbidden & set(document):
            raise SaleBlocked("Конфигурация содержит команду колонке")
        revision = int(document["revision"])  # type: ignore[arg-type]
        with self._factory() as session:
            station = _station(session)
            if revision <= station.config_revision:
                return
            if "sales_blocked" in document:
                station.sales_blocked = bool(document["sales_blocked"])
            for item in document.get("pumps", []):
                if not isinstance(item, dict):
                    continue
                pump = session.get(Pump, str(item.get("pump_id", "")))
                if pump is None or "price_kzt_per_liter" not in item:
                    continue
                pump.price_kzt_per_liter = parse_tenge(item["price_kzt_per_liter"])
            if "rounding_policy_id" in document:
                value = document["rounding_policy_id"]
                station.rounding_policy_id = None if value in {None, ""} else str(value)
            if "fiscal_policy_id" in document:
                value = document["fiscal_policy_id"]
                station.fiscal_policy_id = None if value in {None, ""} else str(value)
            station.config_revision = revision
            session.commit()

    def pending_outbox(self) -> list[dict[str, object]]:
        with self._factory() as session:
            from agzs_station.database.models import OutboxEvent

            rows = session.scalars(
                select(OutboxEvent).where(OutboxEvent.published_at.is_(None)).order_by(OutboxEvent.station_sequence)
            ).all()
            import json

            return [
                {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "schema_version": row.schema_version,
                    "station_sequence": row.station_sequence,
                    "occurred_at": row.created_at,
                    "payload": json.loads(row.payload_json),
                }
                for row in rows
            ]

    def mark_published(self, event_ids: set[str]) -> None:
        with self._factory() as session:
            from agzs_station.database.models import OutboxEvent

            rows = session.scalars(select(OutboxEvent).where(OutboxEvent.event_id.in_(event_ids))).all()
            for row in rows:
                row.published_at = utc_now()
            session.commit()

    def _training(self, name: str, *args: object) -> None:
        if self.env == "production" or not hasattr(self._port, name):
            raise ConfigurationError("Учебные воздействия доступны только в профиле эмулятора")
        getattr(self._port, name)(*args)

    def _expire_reservations(self) -> None:
        now = self._clock()
        with self._factory() as session:
            reserved = session.scalars(select(Pump).where(Pump.status == PumpStatus.RESERVED)).all()
            for pump in reserved:
                changed = datetime.fromisoformat(pump.status_changed_at)
                if changed.tzinfo is None:
                    changed = changed.replace(tzinfo=UTC)
                if (now - changed).total_seconds() < self._reserve_timeout_s:
                    continue
                sale = session.get(Sale, pump.active_sale_id) if pump.active_sale_id else None
                if sale is not None and sale.payment_status not in {PaymentStatus.NONE, PaymentStatus.FAILED}:
                    continue
                if sale is not None and sale.order_status == OrderStatus.DRAFT:
                    _axis(session, sale, "order", OrderStatus.CANCELLED, "Истёк резерв без оплаты")
                pump.active_sale_id = None
                self._manager.transition(session, pump, PumpStatus.IDLE, reason="Истёк резерв")
            session.commit()

    def _apply_payment(self, sale_id: str, key: str, reply: PaymentReply) -> SaleView:
        with self._factory() as session:
            station = _station(session)
            sale = _sale(session, sale_id)
            attempt = session.scalar(select(PaymentAttempt).where(PaymentAttempt.idempotency_key == key))
            pump = session.get(Pump, sale.pump_id)
            if attempt is None or pump is None:
                raise SaleBlocked("Попытка оплаты не найдена")
            if reply.outcome == PaymentOutcome.CONFIRMED and reply.amount_kzt == attempt.amount_kzt:
                attempt.status = PaymentAttemptStatus.CONFIRMED
                attempt.provider_reference = reply.provider_reference
                attempt.updated_at = utc_now()
                _axis(session, sale, "payment", PaymentStatus.CONFIRMED, "Оплата подтверждена", attempt.id)
                if sale.order_status == OrderStatus.DRAFT:
                    _axis(session, sale, "order", OrderStatus.OPEN, "Оплата подтверждена")
                if pump.status == PumpStatus.RESERVED:
                    self._manager.transition(session, pump, PumpStatus.PAYMENT_PENDING, reason="Оплата подтверждена")
                message = "Оплата подтверждена. Чек ещё не открывает газ, пока шлюз закрыт"
            elif reply.outcome == PaymentOutcome.FAILED:
                attempt.status = PaymentAttemptStatus.FAILED
                attempt.updated_at = utc_now()
                _axis(session, sale, "payment", PaymentStatus.FAILED, "Терминал отказал", attempt.id)
                _axis(session, sale, "order", OrderStatus.CANCELLED, "Оплата не прошла")
                pump.active_sale_id = None
                if pump.status in {PumpStatus.RESERVED, PumpStatus.PAYMENT_PENDING}:
                    self._manager.transition(session, pump, PumpStatus.IDLE, reason="Оплата не прошла")
                message = "Оплата отклонена"
            elif reply.outcome == PaymentOutcome.CONFIRMED:
                attempt.status = PaymentAttemptStatus.UNKNOWN
                attempt.updated_at = utc_now()
                _axis(session, sale, "payment", PaymentStatus.UNKNOWN, "Сумма ответа не совпала", attempt.id)
                _incident(session, station, IncidentSeverity.CRITICAL, "PAYMENT_AMOUNT", "Сумма терминала не равна предоплате", sale_id=sale.id)
                message = "Сумма не совпала, отпуск закрыт"
            else:
                attempt.status = PaymentAttemptStatus.UNKNOWN
                attempt.updated_at = utc_now()
                _axis(session, sale, "payment", PaymentStatus.UNKNOWN, "Ответ терминала неизвестен", attempt.id)
                if pump.status == PumpStatus.RESERVED:
                    self._manager.transition(session, pump, PumpStatus.PAYMENT_PENDING, reason="Оплата отправлена без ответа")
                message = "Ответ оплаты неизвестен. Повторное списание запрещено"
            _queue_sale(session, station, sale, SyncEventType.PAYMENT_UPDATED)
            session.commit()
            return _view(sale, message)

    def _mark_training_fiscal(self, session: Session, sale: Sale, policy: object, decision: GateDecision) -> None:
        sale.fiscal_policy_id = getattr(policy, "policy_id", "DENY_ALL")
        _axis(session, sale, "fiscal", FiscalStatus.COMPLETED, decision.reason)

    def _arm(self, session: Session, station: StationProfile, sale: Sale, pump: Pump | None) -> str:
        if pump is None:
            raise SaleBlocked("Колонка не найдена")
        self._manager.transition(session, pump, PumpStatus.READY, reason="Шлюзы отпуска открыты")
        attempt_no = 1
        authorization = DispensingAuthorization(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.station_id,
            sale_id=sale.id,
            pump_id=pump.id,
            attempt_no=attempt_no,
            idempotency_key=f"{station.station_id}:{sale.id}:authorize:{attempt_no}",
            status=AuthorizationStatus.CREATED,
            authorized_kzt=sale.prepaid_kzt,
        )
        session.add(authorization)
        session.flush()
        enqueue(
            session,
            organization_id=station.organization_id,
            station_id=station.station_id,
            event_type=SyncEventType.DISPENSING_AUTHORIZED,
            payload={
                "sale_id": sale.id,
                "authorization_id": authorization.id,
                "status": AuthorizationStatus.CREATED,
                "authorized_kzt": sale.prepaid_kzt,
            },
        )
        return authorization.id

    def _after_authorization(self, sale_id: str, outcome: AdapterOutcome) -> SaleView:
        with self._factory() as session:
            station = _station(session)
            sale = _sale(session, sale_id)
            if outcome == AdapterOutcome.ACKNOWLEDGED:
                _axis(session, sale, "dispensing", DispensingStatus.AUTHORIZED, "Допуск подтверждён")
                message = "Допуск принят"
            elif outcome == AdapterOutcome.REJECTED:
                _axis(session, sale, "dispensing", DispensingStatus.FAILED, "Допуск отклонён")
                message = "Колонка отклонила допуск"
            else:
                _axis(session, sale, "dispensing", DispensingStatus.UNKNOWN, "Ответ допуска неизвестен")
                message = "Неизвестно, принят ли допуск. Повторный пуск запрещён"
            _queue_sale(session, station, sale, SyncEventType.DISPENSING_AUTHORIZED)
            session.commit()
            return _view(sale, message)

    def _finish(self, session: Session, sale: Sale, pump: Pump) -> None:
        station = _station(session)
        authorization = session.scalar(
            select(DispensingAuthorization).where(
                DispensingAuthorization.sale_id == sale.id,
                DispensingAuthorization.status == AuthorizationStatus.ACKNOWLEDGED,
            )
        )
        if authorization is None or authorization.totalizer_start_ml is None:
            _axis(session, sale, "dispensing", DispensingStatus.UNKNOWN, "Нет стартового счётчика")
            return
        try:
            reading = self._manager.read_totalizer_at(pump.link_id, pump.protocol_address)
        except Exception:  # noqa: BLE001
            reading = None
        if reading is None or reading.total_ml is None or reading.observation_quality != ObservationQuality.CONFIRMED:
            _axis(session, sale, "dispensing", DispensingStatus.UNKNOWN, "Конечный счётчик не прочитан")
            return
        delivered = reading.total_ml - authorization.totalizer_start_ml
        if delivered < 0:
            _incident(session, station, IncidentSeverity.CRITICAL, "TOTALIZER_REGRESSION", "Счётчик уменьшился", sale_id=sale.id, pump_id=pump.id)
            _axis(session, sale, "dispensing", DispensingStatus.FAILED, "Счётчик уменьшился")
            self._manager.transition(session, pump, PumpStatus.ERROR, reason="Счётчик уменьшился")
            return
        policy_id = sale.rounding_policy_id or ""
        try:
            money = actual_kzt(delivered, sale.unit_price_kzt_per_liter, policy_id)
        except ValueError as exc:
            _axis(session, sale, "settlement", SettlementStatus.NOT_READY, str(exc))
            return
        settlement, delta, over = classify(sale.prepaid_kzt, money)
        end_reason = DispenseEndReason.STOPPED if pump.status == PumpStatus.STOPPED else DispenseEndReason.COMPLETED
        if over:
            end_reason = DispenseEndReason.ERROR
        session.add(
            DispensingResult(
                sale_id=sale.id,
                organization_id=station.organization_id,
                station_id=station.station_id,
                authorization_id=authorization.id,
                totalizer_end_ml=reading.total_ml,
                delivered_ml=delivered,
                actual_kzt=money,
                end_reason=end_reason,
                finished_at=utc_now(),
            )
        )
        sale.delivered_ml = delivered
        sale.actual_kzt = money
        sale.delta_kzt = delta
        sale.closed_at = utc_now()
        _axis(session, sale, "dispensing", DispensingStatus.STOPPED if end_reason == DispenseEndReason.STOPPED else DispensingStatus.COMPLETED, "Факт снят со счётчика")
        _axis(session, sale, "settlement", settlement, "Сравнение предоплаты и факта")
        _axis(session, sale, "order", OrderStatus.CLOSED, "Отпуск завершён")
        pump.active_sale_id = None
        if over:
            _incident(
                session,
                station,
                IncidentSeverity.CRITICAL,
                "OVER_DELIVERY",
                "Фактическая стоимость больше предоплаты",
                sale_id=sale.id,
                pump_id=pump.id,
            )
            enqueue(
                session,
                organization_id=station.organization_id,
                station_id=station.station_id,
                event_type=SyncEventType.PUMP_FAULT,
                payload={"sale_id": sale.id, "pump_id": pump.id, "code": "OVER_DELIVERY"},
            )
            self._manager.transition(session, pump, PumpStatus.ERROR, reason="Перелив")
        elif pump.status in {PumpStatus.COMPLETED, PumpStatus.STOPPED}:
            self._manager.transition(session, pump, PumpStatus.IDLE, reason="Результат записан")
        if getattr(self._port, "driver_id", "") == "emulator" and hasattr(self._port, "release"):
            self._port.release(pump.protocol_address)  # type: ignore[attr-defined]
        enqueue(
            session,
            organization_id=station.organization_id,
            station_id=station.station_id,
            event_type=SyncEventType.DISPENSING_FINISHED,
            payload={
                "sale_id": sale.id,
                "delivered_ml": delivered,
                "actual_kzt": money,
                "delta_kzt": delta,
                "end_reason": end_reason,
            },
        )
        _queue_sale(session, station, sale, SyncEventType.SALE_CLOSED)
        if session.scalar(select(Refund).where(Refund.sale_id == sale.id)) is not None:
            return

    def _policy(self, policy_id: str | None) -> object:
        if self.env == "production" or not policy_id:
            return DenyAllFiscalPolicy()
        policy = self._policies.get(policy_id)
        if policy is None or not hasattr(policy, "evaluate"):
            return DenyAllFiscalPolicy()
        return policy

    def _reload(self, sale_id: str, message: str) -> SaleView:
        with self._factory() as session:
            return _view(_sale(session, sale_id), message)

    def _pump_view(self, session: Session, pump: Pump) -> PumpView:
        sale = session.get(Sale, pump.active_sale_id) if pump.active_sale_id else None
        telemetry = self._manager.telemetry.get(pump.id)
        confirmed = pump.observation_quality == ObservationQuality.CONFIRMED
        dose = pump.last_dose_ml if confirmed else None
        totalizer = pump.last_totalizer_ml if confirmed else None
        if confirmed and telemetry is not None and telemetry.observation_quality == ObservationQuality.CONFIRMED:
            dose = telemetry.dose_ml
            if telemetry.totalizer_ml is not None:
                totalizer = telemetry.totalizer_ml
        provisional = None
        if dose is not None and sale is not None and sale.rounding_policy_id == TEST_HALF_UP:
            provisional = actual_kzt(dose, sale.unit_price_kzt_per_liter, TEST_HALF_UP)
        return PumpView(
            pump_id=pump.id,
            name=pump.name,
            address=pump.protocol_address,
            status=pump.status,
            observation_quality=pump.observation_quality,
            price_kzt_per_liter=pump.price_kzt_per_liter,
            dose_ml=dose,
            totalizer_ml=totalizer,
            provisional_kzt=provisional,
            sale_id=None if sale is None else sale.id,
            payment_status=None if sale is None else sale.payment_status,
        )


def _station(session: Session) -> StationProfile:
    station = session.scalar(select(StationProfile).limit(1))
    if station is None:
        raise SaleBlocked("Станция не настроена")
    return station


def _open_shift(session: Session) -> Shift:
    shift = session.scalar(select(Shift).where(Shift.status == ShiftStatus.OPEN))
    if shift is None:
        raise SaleBlocked("Смена не открыта")
    return shift


def _sale(session: Session, sale_id: str) -> Sale:
    sale = session.get(Sale, sale_id)
    if sale is None:
        raise SaleBlocked("Продажа не найдена")
    return sale


def _latest_attempt(session: Session, sale_id: str) -> PaymentAttempt | None:
    return session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.sale_id == sale_id).order_by(PaymentAttempt.attempt_no.desc())
    )


def _new_attempt(session: Session, station: StationProfile, sale: Sale, attempt_no: int) -> PaymentAttempt:
    attempt = PaymentAttempt(
        id=new_id(),
        organization_id=station.organization_id,
        station_id=station.station_id,
        sale_id=sale.id,
        attempt_no=attempt_no,
        idempotency_key=f"{station.station_id}:{sale.id}:payment:{attempt_no}",
        status=PaymentAttemptStatus.CREATED,
        amount_kzt=sale.prepaid_kzt,
        provider="pending",
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    session.add(attempt)
    session.flush()
    return attempt


def _fiscal_document(session: Session, station: StationProfile, sale: Sale, step_code: str) -> FiscalDocument:
    document = FiscalDocument(
        id=new_id(),
        organization_id=station.organization_id,
        station_id=station.station_id,
        sale_id=sale.id,
        step_code=step_code,
        idempotency_key=f"{station.station_id}:{sale.id}:fiscal:{step_code}",
        status=FiscalDocumentStatus.CREATED,
    )
    session.add(document)
    session.flush()
    sale.fiscal_status = FiscalStatus.PENDING
    return document


def _axis(
    session: Session,
    sale: Sale,
    axis: str,
    target: str,
    reason: str,
    payment_attempt_id: str | None = None,
) -> None:
    current = getattr(sale, f"{axis}_status")
    if current == target:
        return
    append_sale_event(
        session,
        sale_id=sale.id,
        axis=axis,
        from_value=current,
        to_value=target,
        reason=reason,
        payment_attempt_id=payment_attempt_id,
    )
    setattr(sale, f"{axis}_status", target)


def _queue_sale(session: Session, station: StationProfile, sale: Sale, event_type: str) -> None:
    sale.sync_status = SyncStatus.PENDING
    enqueue(
        session,
        organization_id=station.organization_id,
        station_id=station.station_id,
        event_type=event_type,
        payload={
            "sale_id": sale.id,
            "organization_id": station.organization_id,
            "station_id": station.station_id,
            "shift_id": sale.shift_id,
            "pump_id": sale.pump_id,
            "display_number": sale.display_number,
            "order_status": sale.order_status,
            "payment_status": sale.payment_status,
            "fiscal_status": sale.fiscal_status,
            "dispensing_status": sale.dispensing_status,
            "settlement_status": sale.settlement_status,
            "prepaid_kzt": sale.prepaid_kzt,
            "unit_price_kzt_per_liter": sale.unit_price_kzt_per_liter,
            "delivered_ml": sale.delivered_ml,
            "actual_kzt": sale.actual_kzt,
            "delta_kzt": sale.delta_kzt,
        },
    )


def _gate_refusal(sale: Sale, pump: Pump | None, link: Link | None, port: object) -> str | None:
    if pump is None or link is None:
        return "Колонка не найдена"
    if pump.status != PumpStatus.PAYMENT_PENDING:
        return "Колонка не ждёт открытия шлюза"
    if pump.observation_quality != ObservationQuality.CONFIRMED:
        return "Состояние колонки не подтверждено"
    if sale.payment_status != PaymentStatus.CONFIRMED:
        return "Оплата не подтверждена"
    if sale.prepaid_kzt <= 0:
        return "Сумма предоплаты должна быть больше нуля"
    if not link.control_enabled or not getattr(port, "supports_control", False):
        return "Управление звеном не допущено"
    return None


def _latched(session: Session) -> bool:
    row = session.get(Setting, "command_latch")
    return row is not None and row.value_json == "true"


def _incident(
    session: Session,
    station: StationProfile,
    severity: str,
    code: str,
    message: str,
    *,
    sale_id: str | None = None,
    pump_id: str | None = None,
    once: bool = False,
) -> None:
    if once:
        existing = session.scalar(
            select(Incident).where(
                Incident.code == code,
                Incident.pump_id == pump_id,
                Incident.sale_id == sale_id,
                Incident.acknowledged_at.is_(None),
            )
        )
        if existing is not None:
            return
    session.add(
        Incident(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.station_id,
            severity=severity,
            code=code,
            message=message,
            sale_id=sale_id,
            pump_id=pump_id,
            occurred_at=utc_now(),
        )
    )


def _view(sale: Sale, message: str) -> SaleView:
    return SaleView(
        sale_id=sale.id,
        display_number=sale.display_number,
        pump_id=sale.pump_id,
        order_status=sale.order_status,
        payment_status=sale.payment_status,
        fiscal_status=sale.fiscal_status,
        dispensing_status=sale.dispensing_status,
        settlement_status=sale.settlement_status,
        prepaid_kzt=sale.prepaid_kzt,
        unit_price_kzt_per_liter=sale.unit_price_kzt_per_liter,
        actual_kzt=sale.actual_kzt,
        delta_kzt=sale.delta_kzt,
        delivered_ml=sale.delivered_ml,
        message=message,
    )
