"""Один обмен на звено. Вторая команда ждёт, а не идёт параллельно."""

from collections.abc import Callable
from typing import TypeVar

from agzs_station.errors import LinkBusy

T = TypeVar("T")


class LinkSequencer:
    def __init__(self) -> None:
        self.busy = False
        self.failures = 0

    def exchange(self, operation: Callable[[], T]) -> T:
        if self.busy:
            raise LinkBusy("Звено уже выполняет обмен")
        self.busy = True
        try:
            return operation()
        finally:
            self.busy = False
