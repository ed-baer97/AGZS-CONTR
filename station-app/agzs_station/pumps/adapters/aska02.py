"""Заглушка АСКА-02. Кадров протокола здесь нет и не должно появиться до акта допуска."""

from agzs_station.errors import ExchangeNotPermitted
from agzs_station.pumps.telemetry import AdapterResult, LinkStatus, PumpTelemetry, TotalizerReading

_REFUSAL = "АСКА-02: обмен не допущен"


class Aska02Adapter:
    driver_id = "aska02"
    supports_passive_read = False
    supports_control = False

    def probe(self) -> LinkStatus:
        raise ExchangeNotPermitted(_REFUSAL)

    def read_status(self, address: str) -> PumpTelemetry:
        del address
        raise ExchangeNotPermitted(_REFUSAL)

    def read_totalizer(self, address: str) -> TotalizerReading:
        del address
        raise ExchangeNotPermitted(_REFUSAL)

    def authorize(
        self,
        address: str,
        command_id: str,
        amount_kzt: int,
        *,
        price_kzt_per_liter: int | None = None,
    ) -> AdapterResult:
        del address, command_id, amount_kzt, price_kzt_per_liter
        raise ExchangeNotPermitted(_REFUSAL)

    def read_authorization(self, command_id: str) -> AdapterResult:
        del command_id
        raise ExchangeNotPermitted(_REFUSAL)
