from typing import Protocol

from agzs_station.pumps.telemetry import AdapterResult, LinkStatus, PumpTelemetry, TotalizerReading


class PumpPort(Protocol):
    driver_id: str
    supports_passive_read: bool
    supports_control: bool

    def probe(self) -> LinkStatus: ...

    def read_status(self, address: str) -> PumpTelemetry: ...

    def read_totalizer(self, address: str) -> TotalizerReading: ...

    def authorize(self, address: str, command_id: str, amount_kzt: int) -> AdapterResult: ...

    def read_authorization(self, command_id: str) -> AdapterResult: ...
