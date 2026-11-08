from dataclasses import dataclass


@dataclass(frozen=True)
class SaleView:
    sale_id: str
    display_number: str
    pump_id: str
    order_status: str
    payment_status: str
    fiscal_status: str
    dispensing_status: str
    settlement_status: str
    prepaid_kzt: int
    unit_price_kzt_per_liter: int
    actual_kzt: int | None
    delta_kzt: int | None
    delivered_ml: int | None
    message: str


@dataclass(frozen=True)
class PumpView:
    pump_id: str
    name: str
    address: str
    status: str
    observation_quality: str
    price_kzt_per_liter: int
    dose_ml: int | None
    provisional_kzt: int | None
    sale_id: str | None
    payment_status: str | None


@dataclass(frozen=True)
class ShiftView:
    shift_id: str
    number: int
    status: str


@dataclass(frozen=True)
class IncidentView:
    incident_id: str
    severity: str
    code: str
    message: str
    pump_id: str | None
    sale_id: str | None
