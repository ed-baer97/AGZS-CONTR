"""Заглушка АСКА-02. Кадров протокола здесь нет и не должно появиться до акта допуска."""

from agzs_station.errors import ExchangeNotPermitted

_REFUSAL = "АСКА-02: обмен не допущен"


class Aska02Adapter:
    driver_id = "aska02"
    supports_passive_read = False
    supports_control = False

    def probe(self) -> None:
        raise ExchangeNotPermitted(_REFUSAL)

    def read_status(self, address: str) -> None:
        del address
        raise ExchangeNotPermitted(_REFUSAL)

    def read_totalizer(self, address: str) -> None:
        del address
        raise ExchangeNotPermitted(_REFUSAL)

    def authorize(self, address: str, command_id: str, amount_kzt: int) -> None:
        del address, command_id, amount_kzt
        raise ExchangeNotPermitted(_REFUSAL)

    def read_authorization(self, command_id: str) -> None:
        del command_id
        raise ExchangeNotPermitted(_REFUSAL)
