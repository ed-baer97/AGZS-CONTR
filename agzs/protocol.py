"""Кадр универсального протокола «Искра» 1.72.

Колонка УЗГС-01 в режиме внешнего управления (адаптер АСКА-01 или USB-блок
Технопроект) говорит этим кадром на 9600 8N1. Суммарный счётчик, температура,
коды 01–04 с табло и вид оплаты в кадр не входят.
"""

from __future__ import annotations

from dataclasses import dataclass

SOH = 0x01
STX = 0x02
ETX = 0x03
FRAME_LEN = 23

COMMANDS = {
    "1": "Доза",
    "3": "Установка",
    "4": "Тест",
    "5": "Пуск",
    "6": "Останов",
    "7": "Сброс",
    "9": "До полного бака",
}

STATUS = {
    "01": "Готовность к пуску",
    "02": "Остаток дозы",
    "03": "Пуск",
    "04": "Останов",
    "05": "Сброс",
    "06": "Авария",
    "07": "До полного бака останов",
}

PROTOCOL_ERRORS = {
    "00": "нет ошибки",
    "01": "недопустимый номер ТРК",
    "02": "недопустимая команда при данном состоянии",
    "03": "неправильная контрольная сумма",
}

# Коды, которые пульт УЗСГ-01 показывает сам. В кадре Искра их нет:
# авария приходит одним кодом 06.
PANEL_ERRORS = {
    "01": "Ошибка последовательности импульсов от турбинки",
    "02": "Ошибка работы датчика импульсов турбинки",
    "03": "Переполнение счётчика текущей заправки (999 л)",
    "04": "Превышение максимально возможной скорости заправки",
}


class ProtocolError(Exception):
    pass


@dataclass(frozen=True)
class Frame:
    trk: int
    command: str
    price_tiyn: int
    volume_ml: int
    error: str
    code: str

    def encode(self) -> bytes:
        return encode_frame(self)


def xor_crc(payload: bytes) -> int:
    value = 0
    for byte in payload:
        value ^= byte
    return value


def _digits(value: int, width: int, label: str) -> str:
    if value < 0 or value > int("9" * width):
        raise ProtocolError(f"{label} не влезает в {width} знаков: {value}")
    return f"{value:0{width}d}"


def encode_frame(frame: Frame) -> bytes:
    if frame.command not in COMMANDS:
        raise ProtocolError(f"Неизвестная команда: {frame.command}")
    if frame.trk == 0 and frame.command != "7":
        raise ProtocolError("Адрес 00 допустим только для общего останова")
    if not 0 <= frame.trk <= 99:
        raise ProtocolError(f"Адрес колонки вне 00–99: {frame.trk}")
    for label, field in (("error", frame.error), ("code", frame.code)):
        if len(field) != 2 or any(ch not in "0123456789ABCDEFabcdef" for ch in field):
            raise ProtocolError(f"Поле {label} должно быть двумя hex-знаками")
    body = (
        f"{frame.trk:02d}".encode("ascii")
        + frame.command.encode("ascii")
        + bytes([STX])
        + _digits(frame.price_tiyn, 6, "цена").encode("ascii")
        + _digits(frame.volume_ml, 6, "объём").encode("ascii")
        + frame.error.upper().encode("ascii")
        + frame.code.upper().encode("ascii")
        + bytes([ETX])
    )
    return bytes([SOH]) + body + bytes([xor_crc(body)])


def decode_frame(raw: bytes) -> Frame:
    if len(raw) != FRAME_LEN:
        raise ProtocolError(f"Длина кадра {len(raw)}, нужна {FRAME_LEN}")
    if raw[0] != SOH or raw[4] != STX or raw[21] != ETX:
        raise ProtocolError("В кадре нет SOH/STX/ETX на своих местах")
    if xor_crc(raw[1:22]) != raw[22]:
        raise ProtocolError("Неправильная контрольная сумма")
    try:
        trk = int(raw[1:3].decode("ascii"))
        command = raw[3:4].decode("ascii")
        price = int(raw[5:11].decode("ascii"))
        volume = int(raw[11:17].decode("ascii"))
        error = raw[17:19].decode("ascii").upper()
        code = raw[19:21].decode("ascii").upper()
    except (UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("В числовых полях кадра не цифры") from exc
    if command not in COMMANDS:
        raise ProtocolError(f"Неизвестная команда в ответе: {command}")
    return Frame(trk, command, price, volume, error, code)
