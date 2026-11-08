"""Поведение колонки УЗГС-01 в той части, которую видно по кадру Искра 1.72.

Поле объёма — остаток дозы. Исключение из описания протокола, п. 5.2:
в режиме «до полного бака» в том же поле лежит уже отпущенный объём.
"""

from __future__ import annotations

from agzs.protocol import COMMANDS, Frame, STATUS


class Dispenser:
    def __init__(self, number: int = 1, price_tiyn: int = 0, totalizer_ml: int = 0):
        if not 1 <= number <= 99:
            raise ValueError("Номер колонки 1–99")
        self.number = number
        self.price_tiyn = price_tiyn
        self.totalizer_ml = totalizer_ml
        self.code = "05"
        self.mode: str | None = None  # dose | full
        self.ordered_ml = 0
        self.remaining_ml = 0
        self.dispensed_ml = 0
        self.full_target_ml: int | None = None

    def reported_volume(self) -> int:
        # п. 5.2: при «до полного» в поле объёма — отпущенные миллилитры.
        # Код 06 тоже: иначе авария затрёт уже выданный объём нулевым остатком.
        if self.mode == "full" and self.code in {"03", "06", "07"}:
            return self.dispensed_ml
        if self.code == "05":
            return 0
        return self.remaining_ml

    def reply(self, command: str, error: str = "00") -> Frame:
        return Frame(
            self.number,
            command,
            self.price_tiyn,
            self.reported_volume(),
            error,
            self.code,
        )

    def handle(self, frame: Frame) -> Frame | None:
        if frame.trk == 0 and frame.command == "7":
            if self.code == "03":
                self.code = "07" if self.mode == "full" else "04"
            return None
        if frame.trk != self.number:
            return self.reply(frame.command if frame.command in COMMANDS else "4", "01")
        handler = {
            "1": self._dose,
            "3": self._setup,
            "4": self._test,
            "5": self._start,
            "6": self._stop,
            "7": self._reset,
            "9": self._full,
        }[frame.command]
        return handler(frame)

    def _test(self, frame: Frame) -> Frame:
        return self.reply("4")

    def _dose(self, frame: Frame) -> Frame:
        if self.code != "05" or frame.volume_ml <= 0 or frame.price_tiyn <= 0:
            return self.reply("1", "02")
        self.price_tiyn = frame.price_tiyn
        self.ordered_ml = frame.volume_ml
        self.remaining_ml = frame.volume_ml
        self.dispensed_ml = 0
        self.mode = "dose"
        self.full_target_ml = None
        self.code = "01"
        return self.reply("1")

    def _full(self, frame: Frame) -> Frame:
        if self.code != "05" or frame.price_tiyn <= 0 or frame.volume_ml != 0:
            return self.reply("9", "02")
        self.price_tiyn = frame.price_tiyn
        self.ordered_ml = 0
        self.remaining_ml = 0
        self.dispensed_ml = 0
        self.mode = "full"
        self.full_target_ml = None
        self.code = "01"
        return self.reply("9")

    def _setup(self, frame: Frame) -> Frame:
        if self.code != "05":
            return self.reply("3", "02")
        if frame.price_tiyn > 0:
            self.price_tiyn = frame.price_tiyn
        return self.reply("3")

    def _start(self, frame: Frame) -> Frame:
        if self.code not in {"01", "04"}:
            return self.reply("5", "02")
        self.code = "03"
        return self.reply("5")

    def _stop(self, frame: Frame) -> Frame:
        if self.code != "03":
            return self.reply("6", "02")
        self.code = "07" if self.mode == "full" else "04"
        return self.reply("6")

    def _reset(self, frame: Frame) -> Frame:
        self.code = "05"
        self.mode = None
        self.ordered_ml = 0
        self.remaining_ml = 0
        self.dispensed_ml = 0
        self.full_target_ml = None
        return self.reply("7")

    def set_full_target(self, ml: int) -> None:
        """Объём до отсечки. Есть только у имитатора: живой кадр его не сообщает."""
        if ml <= 0:
            raise ValueError("Объём до отсечки должен быть больше нуля")
        self.full_target_ml = ml

    def pump(self, ml: int) -> int:
        if self.code != "03" or ml <= 0:
            return 0
        if self.mode == "full":
            room = ml
            if self.full_target_ml is not None:
                room = min(ml, self.full_target_ml - self.dispensed_ml)
            if room <= 0:
                self.code = "07"
                return 0
            self.dispensed_ml += room
            self.totalizer_ml += room
            if self.full_target_ml is not None and self.dispensed_ml >= self.full_target_ml:
                self.code = "07"
            return room
        take = min(ml, self.remaining_ml)
        self.remaining_ml -= take
        self.dispensed_ml += take
        self.totalizer_ml += take
        if self.remaining_ml == 0 and take > 0:
            self.code = "04"
        return take

    def local_stop(self) -> bool:
        """Кнопка СТОП на колонке или отсечка по скорости. Причина в кадр не пишется."""
        if self.code != "03":
            return False
        self.code = "07" if self.mode == "full" else "04"
        return True

    def fault(self) -> None:
        self.code = "06"

    @property
    def status_text(self) -> str:
        return STATUS.get(self.code, self.code)
