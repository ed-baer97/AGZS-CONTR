from typing import Protocol


class PumpPort(Protocol):
    driver_id: str
    supports_passive_read: bool
    supports_control: bool

    def probe(self) -> None: ...

    def read_status(self, address: str) -> None: ...

    def read_totalizer(self, address: str) -> None: ...

    def authorize(self, address: str, command_id: str, amount_kzt: int) -> None: ...

    def read_authorization(self, command_id: str) -> None: ...
