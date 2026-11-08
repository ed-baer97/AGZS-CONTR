"""Виртуальные колонки в памяти процесса.

Конструктор не принимает имя устройства и сетевой адрес.
Пока связь оборвана, модель продолжает считать миллилитры, но наружу
отдаёт качество UNKNOWN. Испытательная цена и округление TEST_HALF_UP
ограничивают дозу суммой допуска и не являются правилом боевой кассы.
"""

from dataclasses import dataclass

from agzs_shared.enums import ObservationQuality, PumpStatus
from agzs_shared.money import max_ml_for_prepaid, parse_tenge
from agzs_station.errors import UnknownPumpAddress
from agzs_station.pumps.telemetry import (
    AdapterOutcome,
    AdapterResult,
    LinkStatus,
    PumpTelemetry,
    TotalizerReading,
)


class Clock:
    def now_ms(self) -> int:
        raise NotImplementedError


class ManualClock(Clock):
    """Часы, которые тест двигает сам."""

    def __init__(self) -> None:
        self._now_ms = 0

    def now_ms(self) -> int:
        return self._now_ms

    def advance_ms(self, millis: int) -> None:
        if isinstance(millis, bool) or not isinstance(millis, int) or millis < 0:
            raise ValueError("Сдвиг часов задаётся неотрицательным целым числом миллисекунд")
        self._now_ms += millis


class SystemClock(Clock):
    def now_ms(self) -> int:
        # Монотонные миллисекунды, без календаря и без обращения к оборудованию.
        import time

        return time.monotonic_ns() // 1_000_000


@dataclass
class _Dose:
    command_id: str
    amount_kzt: int
    target_ml: int
    start_totalizer_ml: int
    stopped: bool = False


@dataclass
class _Record:
    result: AdapterResult
    dose: _Dose | None


class _VirtualPump:
    def __init__(self, address: str, flow_ml_per_s: int, price_kzt_per_liter: int, now_ms: int) -> None:
        self.address = address
        self.flow_ml_per_s = flow_ml_per_s
        self.price_kzt_per_liter = price_kzt_per_liter
        self.powered = True
        self.reachable = True
        self.fault_code: str | None = None
        self.totalizer_ml = 0
        self.residue_ms = 0
        self.last_ms = now_ms
        self.dose: _Dose | None = None

    def sync(self, now_ms: int) -> None:
        elapsed = now_ms - self.last_ms
        if elapsed < 0:
            elapsed = 0
        self.last_ms = now_ms
        if elapsed == 0 or not self._flowing():
            return
        self.residue_ms += self.flow_ml_per_s * elapsed
        added = self.residue_ms // 1000
        self.residue_ms %= 1000
        if self.dose is None:
            raise RuntimeError("Отпуск без допуска не считается")
        room = self.dose.target_ml - (self.totalizer_ml - self.dose.start_totalizer_ml)
        if added >= room:
            added = room
            self.residue_ms = 0
        self.totalizer_ml += added

    def _flowing(self) -> bool:
        if self.dose is None or self.dose.stopped or not self.powered or self.fault_code is not None:
            return False
        delivered = self.totalizer_ml - self.dose.start_totalizer_ml
        return delivered < self.dose.target_ml

    def delivered_ml(self) -> int | None:
        if self.dose is None:
            return None
        return self.totalizer_ml - self.dose.start_totalizer_ml


class EmulatorAdapter:
    driver_id = "emulator"
    supports_passive_read = True
    supports_control = True

    def __init__(
        self,
        *,
        pump_count: int = 1,
        flow_ml_per_s: int = 100,
        price_kzt_per_liter: int = 100,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(pump_count, bool) or not isinstance(pump_count, int) or not 1 <= pump_count <= 6:
            raise ValueError("Эмулятор принимает от 1 до 6 колонок")
        if isinstance(flow_ml_per_s, bool) or not isinstance(flow_ml_per_s, int) or flow_ml_per_s <= 0:
            raise ValueError("Скорость задаётся положительным целым числом миллилитров в секунду")
        self._price = parse_tenge(price_kzt_per_liter)
        if self._price == 0:
            raise ValueError("Испытательная цена должна быть больше нуля")
        self._clock = clock or SystemClock()
        now = self._clock.now_ms()
        self._link_up = True
        self._pumps = {
            str(number): _VirtualPump(str(number), flow_ml_per_s, self._price, now)
            for number in range(1, pump_count + 1)
        }
        self._records: dict[str, _Record] = {}

    def probe(self) -> LinkStatus:
        return LinkStatus(up=self._link_up)

    def read_status(self, address: str) -> PumpTelemetry:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        if not self._visible(pump):
            return PumpTelemetry(
                status=PumpStatus.OFFLINE,
                observation_quality=ObservationQuality.UNKNOWN,
                dose_ml=None,
                totalizer_ml=None,
                fault_code=None,
            )
        status = self._status(pump)
        return PumpTelemetry(
            status=status,
            observation_quality=ObservationQuality.CONFIRMED,
            dose_ml=pump.delivered_ml(),
            totalizer_ml=pump.totalizer_ml,
            fault_code=pump.fault_code,
        )

    def read_totalizer(self, address: str) -> TotalizerReading:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        if not self._visible(pump):
            return TotalizerReading(total_ml=None, observation_quality=ObservationQuality.UNKNOWN)
        return TotalizerReading(
            total_ml=pump.totalizer_ml,
            observation_quality=ObservationQuality.CONFIRMED,
        )

    def authorize(
        self,
        address: str,
        command_id: str,
        amount_kzt: int,
        *,
        price_kzt_per_liter: int | None = None,
    ) -> AdapterResult:
        if not isinstance(command_id, str) or not command_id.strip():
            raise ValueError("У допуска должен быть непустой command_id")
        amount = parse_tenge(amount_kzt)
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        known = self._records.get(command_id)
        if known is not None:
            return known.result
        if not self._visible(pump):
            return self._remember(command_id, AdapterOutcome.UNKNOWN, None, None)
        if (
            not pump.powered
            or pump.fault_code is not None
            or amount == 0
            or self._status(pump) == PumpStatus.DISPENSING
        ):
            return self._remember(command_id, AdapterOutcome.REJECTED, None, None)
        price = pump.price_kzt_per_liter
        if price_kzt_per_liter is not None:
            price = parse_tenge(price_kzt_per_liter)
        target_ml = max_ml_for_prepaid(amount, price)
        if target_ml <= 0:
            return self._remember(command_id, AdapterOutcome.REJECTED, None, None)
        if pump.dose is not None:
            return self._remember(command_id, AdapterOutcome.REJECTED, None, None)
        dose = _Dose(
            command_id=command_id,
            amount_kzt=amount,
            target_ml=target_ml,
            start_totalizer_ml=pump.totalizer_ml,
        )
        result = AdapterResult(
            outcome=AdapterOutcome.ACKNOWLEDGED,
            command_id=command_id,
            totalizer_start_ml=pump.totalizer_ml,
        )
        pump.dose = dose
        self._records[command_id] = _Record(result=result, dose=dose)
        return result

    def read_authorization(self, command_id: str) -> AdapterResult:
        known = self._records.get(command_id)
        if known is None:
            return AdapterResult(outcome=AdapterOutcome.UNKNOWN, command_id=command_id)
        return known.result

    def advance_ms(self, millis: int) -> None:
        clock = self._clock
        if not isinstance(clock, ManualClock):
            raise RuntimeError("Сдвигать можно только испытательные часы")
        clock.advance_ms(millis)
        now = clock.now_ms()
        for pump in self._pumps.values():
            pump.sync(now)

    def set_powered(self, address: str, powered: bool) -> None:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        pump.powered = powered
        if not powered and pump.dose is not None:
            pump.dose.stopped = True
            pump.residue_ms = 0

    def set_flow_ml_per_s(self, address: str, flow_ml_per_s: int) -> None:
        if isinstance(flow_ml_per_s, bool) or not isinstance(flow_ml_per_s, int) or flow_ml_per_s <= 0:
            raise ValueError("Скорость задаётся положительным целым числом миллилитров в секунду")
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        pump.flow_ml_per_s = flow_ml_per_s

    def cut_link(self) -> None:
        self._sync_all()
        self._link_up = False

    def restore_link(self) -> None:
        self._link_up = True
        self._sync_all()

    def cut_pump(self, address: str) -> None:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        pump.reachable = False

    def restore_pump(self, address: str) -> None:
        pump = self._pump(address)
        pump.reachable = True
        pump.sync(self._clock.now_ms())

    def inject_fault(self, address: str, code: str) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("У аварии должен быть код")
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        pump.fault_code = code.strip()
        if pump.dose is not None:
            pump.dose.stopped = True
            pump.residue_ms = 0

    def clear_fault(self, address: str) -> None:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        pump.fault_code = None

    def stop_early(self, address: str) -> None:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        if pump.dose is None:
            raise ValueError("Останавливать нечего: допуск не принят")
        pump.dose.stopped = True
        pump.residue_ms = 0

    def release(self, address: str) -> None:
        pump = self._pump(address)
        pump.sync(self._clock.now_ms())
        if self._status(pump) == PumpStatus.DISPENSING:
            raise ValueError("Нельзя снять колонку, пока идёт отпуск")
        pump.dose = None
        pump.residue_ms = 0

    def _remember(
        self,
        command_id: str,
        outcome: AdapterOutcome,
        start_ml: int | None,
        dose: _Dose | None,
    ) -> AdapterResult:
        result = AdapterResult(outcome=outcome, command_id=command_id, totalizer_start_ml=start_ml)
        self._records[command_id] = _Record(result=result, dose=dose)
        return result

    def _pump(self, address: str) -> _VirtualPump:
        pump = self._pumps.get(address)
        if pump is None:
            raise UnknownPumpAddress(f"В эмуляторе нет колонки с адресом {address}")
        return pump

    def _visible(self, pump: _VirtualPump) -> bool:
        return self._link_up and pump.reachable

    def _status(self, pump: _VirtualPump) -> PumpStatus:
        if not pump.powered:
            return PumpStatus.OFFLINE
        if pump.fault_code is not None:
            return PumpStatus.ERROR
        if pump.dose is None:
            return PumpStatus.IDLE
        delivered = pump.delivered_ml()
        if delivered is None:
            raise RuntimeError("У допуска нет объёма")
        if pump.dose.stopped and delivered < pump.dose.target_ml:
            return PumpStatus.STOPPED
        if delivered >= pump.dose.target_ml:
            return PumpStatus.COMPLETED
        return PumpStatus.DISPENSING

    def _sync_all(self) -> None:
        now = self._clock.now_ms()
        for pump in self._pumps.values():
            pump.sync(now)
