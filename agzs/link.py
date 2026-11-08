"""Канал до колонки: процессный имитатор или RS-232 адаптера АСКА."""

from __future__ import annotations

import os
import select
import termios

from agzs.dispenser import Dispenser
from agzs.protocol import FRAME_LEN, Frame, ProtocolError, decode_frame


class InProcessLink:
    def __init__(self, dispenser: Dispenser):
        self.dispenser = dispenser

    def exchange(self, frame: Frame) -> Frame | None:
        return self.dispenser.handle(frame)

    def close(self) -> None:
        return None


class SerialLink:
    """9600 8N1, без чётности. Таймаут по умолчанию 1 с: через пульт Технопроект
    рекомендует не меньше 900 мс."""

    def __init__(self, path: str, timeout: float = 1.0):
        self.path = path
        self.timeout = timeout
        self.fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        _raw_9600(self.fd)

    def exchange(self, frame: Frame) -> Frame | None:
        raw = frame.encode()
        termios.tcflush(self.fd, termios.TCIOFLUSH)
        _write_all(self.fd, raw, self.timeout)
        data = _read_exact(self.fd, FRAME_LEN, self.timeout)
        try:
            return decode_frame(data)
        except ProtocolError:
            raise

    def close(self) -> None:
        os.close(self.fd)


def _raw_9600(fd: int) -> None:
    attrs = termios.tcgetattr(fd)
    attrs[0] = 0
    attrs[1] = 0
    attrs[2] = termios.CS8 | termios.CLOCAL | termios.CREAD
    attrs[3] = 0
    attrs[4] = termios.B9600
    attrs[5] = termios.B9600
    attrs[6][termios.VMIN] = 0
    attrs[6][termios.VTIME] = 0
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)


def _write_all(fd: int, data: bytes, timeout: float) -> None:
    view = memoryview(data)
    while view:
        _readable, writable, _exceptional = select.select([], [fd], [], timeout)
        if not writable:
            raise ProtocolError("Колонка не приняла кадр: таймаут записи")
        sent = os.write(fd, view)
        if sent <= 0:
            raise ProtocolError("Колонка не приняла кадр")
        view = view[sent:]


def _read_exact(fd: int, size: int, timeout: float) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            raise ProtocolError(
                f"Нет ответа колонки за {timeout:.1f} с, принято {len(chunks)} байт"
            )
        part = os.read(fd, size - len(chunks))
        if not part:
            raise ProtocolError("Порт закрылся, пока ждали ответ")
        chunks += part
    return bytes(chunks)
