"""Ответы адаптера колонки. Менеджер появится на этапе 4 и сам решит, какой переход допустим."""

from dataclasses import dataclass
from enum import StrEnum

from agzs_shared.enums import ObservationQuality, PumpStatus


class AdapterOutcome(StrEnum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class LinkStatus:
    up: bool


@dataclass(frozen=True)
class PumpTelemetry:
    status: PumpStatus
    observation_quality: ObservationQuality
    dose_ml: int | None
    totalizer_ml: int | None
    fault_code: str | None


@dataclass(frozen=True)
class TotalizerReading:
    total_ml: int | None
    observation_quality: ObservationQuality


@dataclass(frozen=True)
class AdapterResult:
    outcome: AdapterOutcome
    command_id: str
    totalizer_start_ml: int | None = None
