"""Снимок колонок для экрана и планировщика.

Объём наружу показывается только при подтверждённом наблюдении.
Оплата и фискальный чек в этот снимок не входят.
"""

from dataclasses import dataclass

from agzs_station.pumps.telemetry import PumpTelemetry


@dataclass(frozen=True)
class ColumnRef:
    pump_id: str
    channel_no: int
    address: str
    status: str


@dataclass(frozen=True)
class LinkColumns:
    link_id: str
    passive_read_enabled: bool
    max_channels: int
    unavailable: bool
    pumps: tuple[ColumnRef, ...]


@dataclass(frozen=True)
class ColumnReading:
    pump_id: str
    link_id: str
    name: str
    channel_no: int
    address: str
    status: str
    observation_quality: str
    dose_ml: int | None
    totalizer_ml: int | None
    fault_code: str | None
    price_kzt_per_liter: int
    observed_at: str | None
    link_unavailable: bool
    maintenance_latched: bool


@dataclass(frozen=True)
class StatusExchange:
    telemetry: PumpTelemetry | None
    entered_terminal: bool
