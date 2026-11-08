"""Менеджер колонок: автомат, журнал и один обмен на звено.

Kaspi и Webkassa этому модулю неизвестны. В READY колонку переводит сценарий продажи.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agzs_shared.enums import AuthorizationStatus, ObservationQuality, PumpStatus
from agzs_station.database.journal import append_pump_event
from agzs_station.database.models import DispensingAuthorization, Link, Pump
from agzs_station.database.time import utc_now
from agzs_station.domain.states import can_transition, restored_status
from agzs_station.errors import ExchangeNotPermitted, LinkBusy, SaleBlocked
from agzs_station.pumps.sequencer import LinkSequencer
from agzs_station.pumps.telemetry import AdapterOutcome, AdapterResult, PumpTelemetry

_FAILURE_LIMIT = 3
_OPEN_AUTHORIZATIONS = {
    AuthorizationStatus.ACKNOWLEDGED,
    AuthorizationStatus.SENT,
    AuthorizationStatus.UNKNOWN,
    AuthorizationStatus.CREATED,
}


class PumpManager:
    def __init__(self, factory: sessionmaker[Session], port: object) -> None:
        self._factory = factory
        self._port = port
        self._sequencers: dict[str, LinkSequencer] = {}
        self.telemetry: dict[str, PumpTelemetry] = {}

    def read_totalizer_at(self, link_id: str, address: str):  # noqa: ANN201
        return self._sequencer(link_id).exchange(lambda: self._port.read_totalizer(address))  # type: ignore[attr-defined]

    def transition(self, session: Session, pump: Pump, target: str, *, reason: str) -> None:
        current = pump.status
        if not can_transition(current, target):
            link = session.get(Link, pump.link_id)
            station_id = link.station_id if link is not None else ""
            append_pump_event(
                session,
                organization_id="",
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
        link = session.get(Link, pump.link_id)
        station_id = link.station_id if link is not None else ""
        from agzs_station.database.models import StationProfile

        profile = session.scalar(select(StationProfile).limit(1))
        organization_id = profile.organization_id if profile is not None else ""
        station_id = profile.station_id if profile is not None else station_id
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
                    result = self._call(authorization.pump_id, session, lambda: self._port.read_authorization(command_id))  # type: ignore[attr-defined]
                except (ExchangeNotPermitted, LinkBusy, Exception):  # noqa: BLE001
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
            if pump.status != PumpStatus.READY or pump.observation_quality != ObservationQuality.CONFIRMED:
                raise SaleBlocked("Допуск разрешён только из READY при подтверждённом наблюдении")
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
            from agzs_station.database.models import Sale

            sale = session.get(Sale, authorization.sale_id)
            price = sale.unit_price_kzt_per_liter if sale is not None else None
            session.commit()
        try:
            result = self._exchange(
                pump_id=authorization.pump_id,
                operation=lambda: self._port.authorize(  # type: ignore[attr-defined]
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

    def read_totalizer(self, pump_id: str):  # noqa: ANN201
        with self._factory() as session:
            pump = session.get(Pump, pump_id)
            if pump is None:
                raise SaleBlocked("Колонка не найдена")
            address = pump.protocol_address
        return self._exchange(pump_id, lambda: self._port.read_totalizer(address))  # type: ignore[attr-defined]

    def poll(self) -> None:
        with self._factory() as session:
            pumps = session.scalars(select(Pump)).all()
            for pump in pumps:
                link = session.get(Link, pump.link_id)
                if link is None:
                    continue
                sequencer = self._sequencer(link.id)
                try:
                    probed = self._exchange(pump.id, lambda: self._port.probe())  # type: ignore[attr-defined]
                except ExchangeNotPermitted:
                    self._mark_offline(session, pump, ObservationQuality.UNKNOWN)
                    continue
                except Exception:  # noqa: BLE001
                    sequencer.failures += 1
                    if sequencer.failures >= _FAILURE_LIMIT:
                        self._mark_offline(session, pump, ObservationQuality.UNKNOWN)
                    continue
                if not probed.up:
                    self._mark_offline(session, pump, ObservationQuality.UNKNOWN)
                    continue
                try:
                    telemetry = self._exchange(pump.id, lambda: self._port.read_status(pump.protocol_address))  # type: ignore[attr-defined]
                except ExchangeNotPermitted:
                    self._mark_offline(session, pump, ObservationQuality.UNKNOWN)
                    continue
                except Exception:  # noqa: BLE001
                    sequencer.failures += 1
                    if sequencer.failures >= _FAILURE_LIMIT:
                        self._mark_offline(session, pump, ObservationQuality.UNKNOWN)
                    continue
                sequencer.failures = 0
                self.telemetry[pump.id] = telemetry
                try:
                    self._apply_telemetry(session, pump, telemetry)
                except SaleBlocked:
                    continue
            session.commit()

    def _apply_telemetry(self, session: Session, pump: Pump, telemetry: PumpTelemetry) -> None:
        pump.observation_quality = telemetry.observation_quality
        if telemetry.observation_quality != ObservationQuality.CONFIRMED:
            self.transition(session, pump, PumpStatus.OFFLINE, reason="Наблюдение не подтверждено")
            return
        if telemetry.status == PumpStatus.ERROR or telemetry.fault_code:
            self._move(session, pump, PumpStatus.ERROR, telemetry.fault_code or "Авария")
            return
        if telemetry.status == PumpStatus.OFFLINE:
            self.transition(session, pump, PumpStatus.OFFLINE, reason="Колонка выключена")
            return
        if telemetry.status == PumpStatus.DISPENSING:
            self._move(session, pump, PumpStatus.DISPENSING, "Телеметрия отпуска")
            return
        if telemetry.status in {PumpStatus.COMPLETED, PumpStatus.STOPPED}:
            if pump.status == PumpStatus.READY:
                self.transition(session, pump, PumpStatus.DISPENSING, reason="Отпуск начался между опросами")
            self._move(session, pump, telemetry.status, "Телеметрия завершила дозу")
            return
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
            return
        if telemetry.status == PumpStatus.IDLE and pump.status in {
            PumpStatus.RESERVED,
            PumpStatus.PAYMENT_PENDING,
            PumpStatus.READY,
            PumpStatus.MAINTENANCE,
        }:
            return
        if telemetry.status == PumpStatus.IDLE and pump.status in {PumpStatus.COMPLETED, PumpStatus.STOPPED}:
            return

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

    def _mark_offline(self, session: Session, pump: Pump, quality: str) -> None:
        pump.observation_quality = quality
        self.transition(session, pump, PumpStatus.OFFLINE, reason="Звено недоступно")

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
