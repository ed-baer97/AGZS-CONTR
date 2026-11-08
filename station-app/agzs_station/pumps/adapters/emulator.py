"""Место эмулятора. Модель колонок и скорость отпуска — этап 3.

Конструктор не принимает имя порта и сетевой адрес: этот класс не ходит к УЗСГ-01.
"""

from agzs_station.errors import EmulatorNotReady

_PENDING = "Эмулятор колонок подключается на этапе 3"


class EmulatorAdapter:
    driver_id = "emulator"
    supports_passive_read = True
    supports_control = True

    def probe(self) -> None:
        raise EmulatorNotReady(_PENDING)

    def read_status(self, address: str) -> None:
        del address
        raise EmulatorNotReady(_PENDING)

    def read_totalizer(self, address: str) -> None:
        del address
        raise EmulatorNotReady(_PENDING)

    def authorize(self, address: str, command_id: str, amount_kzt: int) -> None:
        del address, command_id, amount_kzt
        raise EmulatorNotReady(_PENDING)

    def read_authorization(self, command_id: str) -> None:
        del command_id
        raise EmulatorNotReady(_PENDING)
