"""Менеджер колонок: автомат, журнал, показания и один обмен на звено.

Модуль не знает про Kaspi и Webkassa. В READY колонку переводит сценарий продажи.
Пока наблюдение не подтверждено, снимок скрывает объём.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agzs_shared.enums import AuthorizationStatus, ObservationQuality, PumpStatus
from agzs_station.database.journal import append_pump_event
from agzs_station.database.models import DispensingAuthorization, Link, Pump, StationProfile
from agzs_station.database.time import utc_now
from agzs_station.domain.states import can_transition, restored_status
from agzs_station.errors import ExchangeNotPermitted, SaleBlocked
from agzs_station.pumps.board import ColumnReading, ColumnRef, LinkColumns, StatusExchange
from agzs_station.pumps.schedule import FAILURE_LIMIT
from agzs_station.pumps.sequencer import LinkSequencer
from agzs_station.pumps.telemetry import AdapterOutcome, AdapterResult, PumpTelemetry

_OPEN_AUTHORIZATIONS = {
    AuthorizationStatus.ACKNOWLEDGED,
    AuthorizationStatus.SENT,
    AuthorizationStatus.UNKNOWN,
    AuthorizationStatus.CREATED,
}
_TERMINAL = {PumpStatus.COMPLETED, PumpStatus.STOPPED}


class PumpManager:
    def __init__(self, factory: sessionmaker[Session], port: object) -> None:
        self._factory = factory
        self._port = port
        self._sequencers: dict[str, LinkSequencer] = {}
        self.telemetry: dict[str, PumpTelemetry] = {}

    def link_ids(self) -> list[str]:
        with self._factory() as session:
            return list(session.scalars(select(Link.id).order_by(Link.name, Link.id)).all())

    def link_unavailable(self, link_id: str) -> bool:
        with self._factory() as session:
            link = session.get(Link, link_id)
            return link is not None and link.unavailable

    def link_columns(self, link_id: str) -> LinkColumns | None:
        with self._factory() as session:
            link = session.get(Link, link_id)
            if link is None:
                return None
            pumps = session.scalars(
                select(Pump).where(Pump.link_id == link_id).order_by(Pump.channel_no)
            ).all()
            return LinkColumns(
                link_id=link.id,
                passive_read_enabled=link.passive_read_enabled,
                max_channels=link.max_channels,
                unavailable=link.unavailable,
                pumps=tuple(
                    ColumnRef(
                        pump_id=pump.id,
                        channel_no=pump.channel_no,
                        address=pump.protocol_address,
                        status=pump.status,
                    )
                    for pump in pumps
                ),
            )

    def readings(self) -> list[ColumnReading]:
        with self._factory() as session:
            pumps = session.scalars(select(Pump).order_by(Pump.channel_no, Pump.name)).all()
            rows: list[ColumnReading] = []
            for pump in pumps:
                link = session.get(Link, pump.link_id)
                confirmed = pump.observation_quality == ObservationQuality.CONFIRMED
                live = self.telemetry.get(pump.id)
                dose = pump.last_dose_ml
                totalizer = pump.last_totalizer_ml
                fault = pump.last_fault_code
                if confirmed and live is not None and live.observation_quality == ObservationQuality.CONFIRMED:
                    dose = live.dose_ml
                    if live.totalizer_ml is not None:
                        totalizer = live.totalizer_ml
                    fault = live.fault_code
                if not confirmed:
                    dose = None
                    totalizer = None
                    fault = None
                rows.append(
                    ColumnReading(
                        pump_id=pump.id,
                        link_id=pump.link_id,
                        name=pump.name,
                        channel_no=pump.channel_no,
                        address=pump.protocol_address,
                        status=pump.status,
                        observation_quality=pump.observation_quality,
                        dose_ml=dose,
                        totalizer_ml=totalizer,
                        fault_code=fault,
                        price_kzt_per_liter=pump.price_kzt_per_liter,
                        observed_at=pump.last_observed_at,
                        link_unavailable=False if link is None else link.unavailable,
                        maintenance_latched=pump.maintenance_latched,
                    )
                )
            return rows

    def read_totalizer_at(self, link_id: str, address: str):  # noqa: ANN201
        return self._sequencer(link_id).exchange(lambda: self._port.read_totalizer(address))  # type: ignore[attr-defined]

    def transition(self, session: Session, pump: Pump, target: str, *, reason: str) -> None:
        current = pump.status
        organization_id, station_id = self._owners(session, pump)
        if not can_transition(current, target):
            append_pump_event(
                session,
                organization_id=organization_id,
                station_id=station_id,
                pump_id=pump.id,
                event_type="transition_rejected",
                from_status=current,
                to_status=target,
                sale_id=pump.active_sale_id,
                payload={"reason": reason},
            )
            raise SaleBlocked(f"Переход {current} → {target} вне схемы")
        if current == target:
            return
        append_pump_event(
            session,
            organization_id=organization_id,
            station_id=station_id,
            pump_id=pump.id,
            event_type="status",
            from_status=current,
            to_status=target,
            sale_id=pump.active_sale_id,
            payload={"reason": reason},
        )
        pump.status = target
        pump.status_changed_at = utc_now()

    def set_maintenance(self, session: Session, pump: Pump, enabled: bool) -> None:
        pump.maintenance_latched = enabled
        if enabled and pump.status in {PumpStatus.IDLE, PumpStatus.ERROR}:
            self.transition(session, pump, PumpStatus.MAINTENANCE, reason="Техник перевёл колонку в обслуживание")
            return
        if enabled and pump.status != PumpStatus.MAINTENANCE:
            organization_id, station_id = self._owners(session, pump)
            append_pump_event(
                session,
                organization_id=organization_id,
                station_id=station_id,
                pump_id=pump.id,
                event_type="maintenance_latched",
                from_status=pump.status,
                to_status=pump.status,
                sale_id=pump.active_sale_id,
                payload={"latched": True},
            )
            return
        if (
            not enabled
            and pump.status == PumpStatus.MAINTENANCE
            and pump.observation_quality == ObservationQuality.CONFIRMED
            and pump.active_sale_id is None
        ):
            self.transition(session, pump, PumpStatus.IDLE, reason="Колонка возвращена в работу")

    def recover(self) -> None:
        """Сверить уже отправленные допуски. Повторный authorize отсюда не вызывается."""

        with self._factory() as session:
            pending = session.scalars(
                select(DispensingAuthorization).where(
                    DispensingAuthorization.status.in_(
                        [AuthorizationStatus.SENT, AuthorizationStatus.UNKNOWN]
                    )
                )
            ).all()
            for authorization in pending:
                command_id = authorization.idempotency_key
                try:
                    result = self._call(
                        authorization.pump_id,
                        session,
                        lambda command_id=command_id: self._port.read_authorization(command_id),  # type: ignore[attr-defined]
                    )
                except (ExchangeNotPermitted, Exception):  # noqa: BLE001
                    result = AdapterResult(outcome=AdapterOutcome.UNKNOWN, command_id=command_id)
                self._apply_authorization_result(session, authorization, result, sent_again=False)
            session.commit()

    def submit_created(self, authorization_id: str) -> AdapterOutcome:
        with self._factory() as session:
            authorization = session.get(DispensingAuthorization, authorization_id)
            if authorization is None:
                raise SaleBlocked("Допуск не найден")
            if authorization.status in {AuthorizationStatus.UNKNOWN, AuthorizationStatus.SENT}:
                return AdapterOutcome(authorization.status)
            if authorization.status != AuthorizationStatus.CREATED:
                return AdapterOutcome(authorization.status)
            pump = session.get(Pump, authorization.pump_id)
            link = session.get(Link, pump.link_id) if pump is not None else None
            if pump is None or link is None:
                raise SaleBlocked("Колонка допуска не найдена")
            if _latched(session) or not link.control_enabled or not getattr(self._port, "supports_control", False):
                authorization.status = AuthorizationStatus.REJECTED
                if pump.status == PumpStatus.READY:
                    self.transition(session, pump, PumpStatus.ERROR, reason="Управление звену не разрешено")
                session.commit()
                return AdapterOutcome.REJECTED
            if pump.channel_no > link.max_channels:
                raise SaleBlocked("Канал колонки не входит в допущенные")
            if pump.status != PumpStatus.READY or pump.observation_quality != ObservationQuality.CONFIRMED:
                raise SaleBlocked("Допуск разрешён только из READY при подтверждённом наблюдении")
            other = session.scalars(
                select(DispensingAuthorization).where(
                    DispensingAuthorization.pump_id == pump.id,
                    DispensingAuthorization.id != authorization.id,
                    DispensingAuthorization.status.in_(list(_OPEN_AUTHORIZATIONS)),
                )
            ).first()
            if other is not None:
                raise SaleBlocked("По колонке уже есть команда управления")
            blocking = session.scalars(
                select(DispensingAuthorization).where(
                    DispensingAuthorization.sale_id == authorization.sale_id,
                    DispensingAuthorization.id != authorization.id,
                    DispensingAuthorization.status.in_(list(_OPEN_AUTHORIZATIONS)),
                )
            ).first()
            if blocking is not None:
                raise SaleBlocked("По продаже уже есть допуск")
            authorization.status = AuthorizationStatus.SENT
            address = pump.protocol_address
            command_id = authorization.idempotency_key
            amount = authorization.authorized_kzt
            pump_id = pump.id
            from agzs_station.database.models import Sale

            sale = session.get(Sale, authorization.sale_id)
            price = sale.unit_price_kzt_per_liter if sale is not None else None
            session.commit()
        try:
            result = self._exchange(
                pump_id,
                lambda: self._port.authorize(  # type: ignore[attr-defined]
                    address,
                    command_id,
                    amount,
                    price_kzt_per_liter=price,
                ),
            )
        except ExchangeNotPermitted:
            result = AdapterResult(outcome=AdapterOutcome.REJECTED, command_id=command_id)
        except Exception:  # noqa: BLE001
            result = AdapterResult(outcome=AdapterOutcome.UNKNOWN, command_id=command_id)
        with self._factory() as session:
            authorization = session.get(DispensingAuthorization, authorization_id)
            if authorization is None:
                return result.outcome
            self._apply_authorization_result(session, authorization, result, sent_again=True)
            session.commit()
            return result.outcome

    def next_control_id(self, link_id: str) -> str | None:
        with self._factory() as session:
            if _latched(session) or not getattr(self._port, "supports_control", False):
                return None
            link = session.get(Link, link_id)
            if link is None or not link.control_enabled or link.unavailable:
                return None
            authorization = session.scalar(
                select(DispensingAuthorization)
                .join(Pump, Pump.id == DispensingAuthorization.pump_id)
                .where(
                    Pump.link_id == link_id,
                    Pump.status == PumpStatus.READY,
                    Pump.observation_quality == ObservationQuality.CONFIRMED,
                    Pump.channel_no <= link.max_channels,
                    DispensingAuthorization.status == AuthorizationStatus.CREATED,
                )
                .order_by(DispensingAuthorization.attempt_no, DispensingAuthorization.id)
            )
            return None if authorization is None else authorization.id

    def read_totalizer(self, pump_id: str):  # noqa: ANN201
        with self._factory() as session:
            pump = session.get(Pump, pump_id)
            if pump is None:
                raise SaleBlocked("Колонка не найдена")
            address = pump.protocol_address
        return self._exchange(pump_id, lambda: self._port.read_totalizer(address))  # type: ignore[attr-defined]

    def poll(self) -> None:
        """Полный опрос для учебной кассы: на эмуляторе нет живой шины, ждать интервал не нужно."""

        for link_id in self.link_ids():
            self.close_unreadable(link_id)
            board = self.link_columns(link_id)
            if board is None or not board.passive_read_enabled or board.max_channels <= 0:
                continue
            if not self.exchange_probe(link_id):
                continue
            board = self.link_columns(link_id)
            if board is None:
                continue
            for pump in board.pumps:
                if pump.channel_no > board.max_channels:
                    continue
                result = self.exchange_status(pump.pump_id)
                if result.entered_terminal:
                    self.capture_totalizer(pump.pump_id, checkpoint="complete")

    def close_unreadable(self, link_id: str) -> None:
        with self._factory() as session:
            link = session.get(Link, link_id)
            if link is None:
                return
            pumps = session.scalars(select(Pump).where(Pump.link_id == link_id)).all()
            for pump in pumps:
                readable = (
                    link.passive_read_enabled
                    and link.max_channels > 0
                    and pump.channel_no <= link.max_channels
                )
                if readable:
                    continue
                reason = (
                    "Пассивное чтение звену не разрешено"
                    if pump.channel_no <= link.max_channels
                    else "Канал вне числа допущенных"
                )
                self._hide(session, pump, reason)
            session.commit()

    def exchange_probe(self, link_id: str) -> bool:
        with self._factory() as session:
            link = session.get(Link, link_id)
            if link is None:
                return False
            pumps = list(session.scalars(select(Pump).where(Pump.link_id == link_id)).all())
            try:
                probed = self._sequencer(link_id).exchange(lambda: self._port.probe())  # type: ignore[attr-defined]
            except ExchangeNotPermitted:
                for pump in pumps:
                    self._hide(session, pump, "Обмен не допущен")
                session.commit()
                return False
            except Exception:  # noqa: BLE001
                self._note_failure(session, link, pumps)
                session.commit()
                return False
            if not probed.up:
                link.unavailable = True
                link.consecutive_failures = FAILURE_LIMIT
                for pump in pumps:
                    if pump.channel_no <= link.max_channels:
                        self._hide(session, pump, "Звено недоступно")
                session.commit()
                return False
            self._note_success(link)
            session.commit()
            return True

    def exchange_status(self, pump_id: str) -> StatusExchange:
        with self._factory() as session:
            pump = session.get(Pump, pump_id)
            if pump is None:
                raise SaleBlocked("Колонка не найдена")
            link = session.get(Link, pump.link_id)
            if link is None:
                raise SaleBlocked("Звено не найдено")
            if not link.passive_read_enabled or pump.channel_no > link.max_channels:
                self._hide(session, pump, "Чтение канала закрыто")
                session.commit()
                return StatusExchange(telemetry=None, entered_terminal=False)
            address = pump.protocol_address
            link_id = link.id
            try:
                telemetry = self._sequencer(link_id).exchange(lambda: self._port.read_status(address))  # type: ignore[attr-defined]
            except ExchangeNotPermitted:
                self._hide(session, pump, "Обмен не допущен")
                session.commit()
                return StatusExchange(telemetry=None, entered_terminal=False)
            except Exception:  # noqa: BLE001
                pumps = list(session.scalars(select(Pump).where(Pump.link_id == link_id)).all())
                self._note_failure(session, link, pumps)
                session.commit()
                return StatusExchange(telemetry=None, entered_terminal=False)
            if telemetry.observation_quality == ObservationQuality.CONFIRMED:
                self._note_success(link)
            self.telemetry[pump.id] = telemetry
            try:
                entered = self._apply_telemetry(session, pump, telemetry)
            except SaleBlocked:
                entered = False
            session.commit()
            return StatusExchange(telemetry=telemetry, entered_terminal=entered)

    def capture_totalizer(self, pump_id: str, *, checkpoint: str | None) -> None:
        with self._factory() as session:
            pump = session.get(Pump, pump_id)
            if pump is None:
                return
            link = session.get(Link, pump.link_id)
            if link is None or not link.passive_read_enabled or pump.channel_no > link.max_channels:
                return
            address = pump.protocol_address
            try:
                reading = self._sequencer(link.id).exchange(lambda: self._port.read_totalizer(address))  # type: ignore[attr-defined]
            except ExchangeNotPermitted:
                self._hide(session, pump, "Обмен не допущен")
                session.commit()
                return
            except Exception:  # noqa: BLE001
                pumps = list(session.scalars(select(Pump).where(Pump.link_id == link.id)).all())
                self._note_failure(session, link, pumps)
                session.commit()
                return
            pump.last_observed_at = utc_now()
            if reading.observation_quality != ObservationQuality.CONFIRMED or reading.total_ml is None:
                pump.observation_quality = reading.observation_quality
                session.commit()
                return
            self._note_success(link)
            pump.last_totalizer_ml = reading.total_ml
            if checkpoint is not None:
                organization_id, station_id = self._owners(session, pump)
                append_pump_event(
                    session,
                    organization_id=organization_id,
                    station_id=station_id,
                    pump_id=pump.id,
                    event_type="totalizer",
                    from_status=pump.status,
                    to_status=pump.status,
                    sale_id=pump.active_sale_id,
                    payload={"phase": checkpoint, "totalizer_ml": reading.total_ml},
                )
            session.commit()

    def _apply_telemetry(self, session: Session, pump: Pump, telemetry: PumpTelemetry) -> bool:
        before = pump.status
        self._remember(pump, telemetry)
        if telemetry.observation_quality != ObservationQuality.CONFIRMED:
            if pump.status != PumpStatus.OFFLINE and can_transition(pump.status, PumpStatus.OFFLINE):
                self.transition(session, pump, PumpStatus.OFFLINE, reason="Наблюдение не подтверждено")
            return False
        if telemetry.status == PumpStatus.ERROR or telemetry.fault_code:
            self._move(session, pump, PumpStatus.ERROR, telemetry.fault_code or "Авария")
            return False
        if telemetry.status == PumpStatus.OFFLINE:
            self.transition(session, pump, PumpStatus.OFFLINE, reason="Колонка выключена")
            return False
        if telemetry.status == PumpStatus.DISPENSING:
            self._move(session, pump, PumpStatus.DISPENSING, "Телеметрия отпуска")
            return False
        if telemetry.status in _TERMINAL:
            if pump.status == PumpStatus.READY:
                self.transition(session, pump, PumpStatus.DISPENSING, reason="Отпуск начался между опросами")
            self._move(session, pump, telemetry.status, "Телеметрия завершила дозу")
            return before not in _TERMINAL and pump.status in _TERMINAL
        if pump.status == PumpStatus.OFFLINE:
            authorization = self._latest_authorization(session, pump.active_sale_id)
            target = restored_status(
                maintenance_latched=pump.maintenance_latched,
                has_sale=pump.active_sale_id is not None,
                payment_status=self._payment_status(session, pump.active_sale_id),
                authorization_status=None if authorization is None else authorization.status,
                physical_status=telemetry.status,
            )
            self.transition(session, pump, target, reason="Связь восстановлена")
            return False
        return False

    def _remember(self, pump: Pump, telemetry: PumpTelemetry) -> None:
        pump.observation_quality = telemetry.observation_quality
        pump.last_observed_at = utc_now()
        if telemetry.observation_quality != ObservationQuality.CONFIRMED:
            pump.last_dose_ml = None
            return
        pump.last_dose_ml = telemetry.dose_ml
        pump.last_fault_code = telemetry.fault_code
        if telemetry.totalizer_ml is not None:
            pump.last_totalizer_ml = telemetry.totalizer_ml

    def _move(self, session: Session, pump: Pump, target: str, reason: str) -> None:
        if pump.status == target or can_transition(pump.status, target):
            self.transition(session, pump, target, reason=reason)
            return
        if can_transition(pump.status, PumpStatus.OFFLINE) and can_transition(PumpStatus.OFFLINE, target):
            self.transition(session, pump, PumpStatus.OFFLINE, reason=reason)
            self.transition(session, pump, target, reason=reason)
            return
        self.transition(session, pump, target, reason=reason)

    def _apply_authorization_result(
        self,
        session: Session,
        authorization: DispensingAuthorization,
        result: AdapterResult,
        *,
        sent_again: bool,
    ) -> None:
        del sent_again
        pump = session.get(Pump, authorization.pump_id)
        if result.outcome == AdapterOutcome.ACKNOWLEDGED:
            authorization.status = AuthorizationStatus.ACKNOWLEDGED
            authorization.totalizer_start_ml = result.totalizer_start_ml
            if pump is not None and result.totalizer_start_ml is not None:
                pump.last_totalizer_ml = result.totalizer_start_ml
                pump.last_observed_at = utc_now()
                organization_id, station_id = self._owners(session, pump)
                append_pump_event(
                    session,
                    organization_id=organization_id,
                    station_id=station_id,
                    pump_id=pump.id,
                    event_type="totalizer",
                    from_status=pump.status,
                    to_status=pump.status,
                    sale_id=pump.active_sale_id,
                    payload={"phase": "authorize", "totalizer_ml": result.totalizer_start_ml},
                )
            return
        if result.outcome == AdapterOutcome.REJECTED:
            authorization.status = AuthorizationStatus.REJECTED
            if pump is not None and pump.status == PumpStatus.READY:
                self.transition(session, pump, PumpStatus.ERROR, reason="Адаптер отказал в допуске")
            return
        authorization.status = AuthorizationStatus.UNKNOWN
        if pump is not None:
            self.transition(session, pump, PumpStatus.OFFLINE, reason="Ответ допуска неизвестен")
            pump.observation_quality = ObservationQuality.UNKNOWN

    def _hide(self, session: Session, pump: Pump, reason: str) -> None:
        was_hidden = (
            pump.observation_quality == ObservationQuality.UNKNOWN and pump.status == PumpStatus.OFFLINE
        )
        pump.observation_quality = ObservationQuality.UNKNOWN
        pump.last_dose_ml = None
        pump.last_observed_at = utc_now()
        if pump.status != PumpStatus.OFFLINE and can_transition(pump.status, PumpStatus.OFFLINE):
            self.transition(session, pump, PumpStatus.OFFLINE, reason=reason)
            return
        if was_hidden:
            return
        organization_id, station_id = self._owners(session, pump)
        append_pump_event(
            session,
            organization_id=organization_id,
            station_id=station_id,
            pump_id=pump.id,
            event_type="observation_hidden",
            from_status=pump.status,
            to_status=pump.status,
            sale_id=pump.active_sale_id,
            payload={"reason": reason},
        )

    def _note_failure(self, session: Session, link: Link, pumps: list[Pump]) -> None:
        link.consecutive_failures += 1
        if link.consecutive_failures < FAILURE_LIMIT:
            return
        link.unavailable = True
        for pump in pumps:
            if pump.channel_no <= link.max_channels:
                self._hide(session, pump, "Звено недоступно")

    def _note_success(self, link: Link) -> None:
        link.consecutive_failures = 0
        link.unavailable = False

    def _latest_authorization(self, session: Session, sale_id: str | None) -> DispensingAuthorization | None:
        if sale_id is None:
            return None
        return session.scalar(
            select(DispensingAuthorization)
            .where(DispensingAuthorization.sale_id == sale_id)
            .order_by(DispensingAuthorization.attempt_no.desc())
        )

    def _payment_status(self, session: Session, sale_id: str | None) -> str | None:
        if sale_id is None:
            return None
        from agzs_station.database.models import Sale

        sale = session.get(Sale, sale_id)
        if sale is None:
            return None
        return sale.payment_status

    def _owners(self, session: Session, pump: Pump) -> tuple[str, str]:
        profile = session.scalar(select(StationProfile).limit(1))
        if profile is not None:
            return profile.organization_id, profile.station_id
        link = session.get(Link, pump.link_id)
        return "", link.station_id if link is not None else ""

    def _call(self, pump_id: str, session: Session, operation):  # noqa: ANN001
        pump = session.get(Pump, pump_id)
        link_id = pump.link_id if pump is not None else ""
        return self._sequencer(link_id).exchange(operation)

    def _exchange(self, pump_id: str, operation):  # noqa: ANN001
        with self._factory() as session:
            pump = session.get(Pump, pump_id)
            link_id = pump.link_id if pump is not None else ""
        return self._sequencer(link_id).exchange(operation)

    def _sequencer(self, link_id: str) -> LinkSequencer:
        sequencer = self._sequencers.get(link_id)
        if sequencer is None:
            sequencer = LinkSequencer()
            self._sequencers[link_id] = sequencer
        return sequencer


def _latched(session: Session) -> bool:
    from agzs_station.database.models import Setting

    row = session.get(Setting, "command_latch")
    return row is not None and row.value_json == "true"
