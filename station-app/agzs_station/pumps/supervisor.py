"""Один обмен на звено за вызов, в порядке из описания колонок.

Сначала колонки, которые уже отпускают. Затем одна команда управления,
если она разрешена. Затем спокойные колонки по кругу. Затем счётчик.
Упавшее звено пробуется не чаще, чем раз в интервал восстановления.
"""

from agzs_shared.enums import ObservationQuality, PumpStatus
from agzs_station.errors import SaleBlocked
from agzs_station.pumps.manager import PumpManager
from agzs_station.pumps.schedule import (
    CALM_INTERVAL_MS,
    DISPENSING_INTERVAL_MS,
    RECOVER_INTERVAL_MS,
    TOTALIZER_INTERVAL_MS,
)


class ColumnSupervisor:
    def __init__(self, manager: PumpManager) -> None:
        self._manager = manager
        self._last_status_ms: dict[str, int] = {}
        self._last_totalizer_ms: dict[str, int] = {}
        self._cursor: dict[str, int] = {}
        self._totalizer_cursor: dict[str, int] = {}
        self._next_recover_ms: dict[str, int] = {}
        self._need_probe: set[str] = set()
        self._force_totalizer: set[str] = set()

    def acquire(self, now_ms: int) -> list[str]:
        actions: list[str] = []
        for link_id in self._manager.link_ids():
            action = self._acquire_link(link_id, now_ms)
            if action is not None:
                actions.append(action)
        return actions

    def _acquire_link(self, link_id: str, now_ms: int) -> str | None:
        self._manager.close_unreadable(link_id)
        board = self._manager.link_columns(link_id)
        if board is None or not board.passive_read_enabled or board.max_channels <= 0:
            return None
        readable = tuple(pump for pump in board.pumps if pump.channel_no <= board.max_channels)
        if board.unavailable:
            if now_ms < self._next_recover_ms.get(link_id, 0):
                return None
            return self._probe(link_id, now_ms)
        if link_id in self._need_probe:
            return self._probe(link_id, now_ms)
        hot = [
            pump
            for pump in readable
            if pump.status == PumpStatus.DISPENSING and self._status_due(pump.pump_id, now_ms, DISPENSING_INTERVAL_MS)
        ]
        if hot:
            return self._read_status(link_id, hot[0].pump_id, hot[0].address, now_ms)
        control_id = self._manager.next_control_id(link_id)
        if control_id is not None:
            try:
                self._manager.submit_created(control_id)
            except SaleBlocked:
                return f"{link_id}:authorize-blocked"
            return f"{link_id}:authorize"
        calm = [
            pump
            for pump in readable
            if pump.status != PumpStatus.DISPENSING and self._status_due(pump.pump_id, now_ms, CALM_INTERVAL_MS)
        ]
        if calm:
            cursor = self._cursor.get(link_id, 0) % len(calm)
            chosen = calm[cursor]
            self._cursor[link_id] = cursor + 1
            return self._read_status(link_id, chosen.pump_id, chosen.address, now_ms)
        meter = self._due_meters(readable, now_ms)
        if not meter:
            return None
        cursor = self._totalizer_cursor.get(link_id, 0) % len(meter)
        chosen = meter[cursor]
        self._totalizer_cursor[link_id] = cursor + 1
        checkpoint = "complete" if chosen.pump_id in self._force_totalizer else None
        self._force_totalizer.discard(chosen.pump_id)
        self._last_totalizer_ms[chosen.pump_id] = now_ms
        self._manager.capture_totalizer(chosen.pump_id, checkpoint=checkpoint)
        return f"{link_id}:totalizer:{chosen.address}"

    def _probe(self, link_id: str, now_ms: int) -> str:
        up = self._manager.exchange_probe(link_id)
        self._need_probe.discard(link_id)
        if not up:
            self._next_recover_ms[link_id] = now_ms + RECOVER_INTERVAL_MS
        return f"{link_id}:probe:{'up' if up else 'down'}"

    def _read_status(self, link_id: str, pump_id: str, address: str, now_ms: int) -> str:
        result = self._manager.exchange_status(pump_id)
        if result.telemetry is None:
            if self._manager.link_unavailable(link_id):
                self._next_recover_ms[link_id] = now_ms + RECOVER_INTERVAL_MS
            return f"{link_id}:status-failed:{address}"
        self._last_status_ms[pump_id] = now_ms
        if result.telemetry.observation_quality != ObservationQuality.CONFIRMED:
            self._need_probe.add(link_id)
        if result.entered_terminal:
            self._force_totalizer.add(pump_id)
        return f"{link_id}:status:{address}"

    def _status_due(self, pump_id: str, now_ms: int, interval_ms: int) -> bool:
        last = self._last_status_ms.get(pump_id)
        return last is None or now_ms - last >= interval_ms

    def _due_meters(self, readable: tuple, now_ms: int) -> list:
        due = []
        for pump in readable:
            last = self._last_totalizer_ms.get(pump.pump_id)
            forced = pump.pump_id in self._force_totalizer
            if forced or last is None or now_ms - last >= TOTALIZER_INTERVAL_MS:
                due.append(pump)
        return due
