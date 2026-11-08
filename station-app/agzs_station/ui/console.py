"""Пульт управления и индикации колонок.

Лицевая панель, клавиатура дозы, пуск и стоп собраны по принципу операторского
места АЗС: у каждой колонки своё табло суммы и дозы, лампа состояния и общий
пост управления. Это не копия чужого интерфейса и не кассовый чек.
"""

from PySide6.QtCore import Qt, QSize, QTimer
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from agzs_station.application.cashier import CashierService
from agzs_station.application.views import PumpView
from agzs_station.ui.indicators import face_digits, lamp_color, preset_kzt, status_caption

_SEGMENTS = {
    "0": "1110111",
    "1": "0010010",
    "2": "1011101",
    "3": "1011011",
    "4": "0111010",
    "5": "1101011",
    "6": "1101111",
    "7": "1010010",
    "8": "1111111",
    "9": "1111011",
    "-": "0001000",
    " ": "0000000",
}

_STYLE = """
QMainWindow, QWidget#console { background: #d5dee8; color: #1c2834; }
QWidget#header { background: #0c3d73; }
QLabel#title { color: white; font-size: 20px; font-weight: 700; }
QLabel#header-note, QLabel#clock, QLabel#shift { color: #d6e6f7; }
QLabel#training-banner { background: #f4e4b2; color: #5c4308; padding: 6px 10px; font-weight: 600; }
QFrame#column-face { background: #f7f9fc; border: 1px solid #8ea0b5; border-radius: 10px; color: #1c2834; }
QFrame#column-face[selected="true"] { border: 3px solid #e0a106; background: #fffdf5; color: #1c2834; }
QFrame#display { background: #07140c; border: 1px solid #163324; border-radius: 6px; }
QLabel#face-name { color: #1c2834; font-weight: 700; font-size: 14px; }
QLabel#face-state { color: #1c2834; font-weight: 700; }
QLabel#face-meta, QLabel#face-total { color: #415066; }
QWidget#deck { background: #e7eef5; border: 1px solid #b7c5d4; border-radius: 10px; color: #1c2834; }
QLabel#dose-entry {
    background: #07140c; color: #7dff67; border-radius: 6px;
    font-family: Consolas, "Courier New"; font-size: 28px; font-weight: 700; padding: 4px 10px;
}
QPushButton#key {
    background: #f4f7fb; border: 1px solid #9aafc4; border-radius: 6px;
    font-size: 18px; font-weight: 700; min-width: 54px; min-height: 46px;
}
QPushButton#key:pressed { background: #c5d4e4; }
QPushButton#key:disabled { color: #9aa8b6; }
QPushButton#start { background: #1f8f45; color: white; font-size: 18px; font-weight: 700; min-height: 64px; border-radius: 8px; }
QPushButton#stop { background: #c0392b; color: white; font-size: 18px; font-weight: 700; min-height: 64px; border-radius: 8px; }
QPushButton#reset, QPushButton#block { background: #efefef; border: 1px solid #9aafc4; border-radius: 6px; font-weight: 700; min-height: 36px; }
QPushButton#mode { background: #f4f7fb; border: 1px solid #9aafc4; border-radius: 6px; min-height: 34px; font-weight: 600; }
QPushButton#mode:checked { background: #0c3d73; color: white; }
QLabel#message {
    background: #102019; color: #b6f5c4; padding: 8px 12px;
    font-family: Consolas, "Courier New"; font-size: 14px;
}
QPushButton#quiet { background: transparent; color: white; border: 1px solid #7ea0c8; border-radius: 4px; padding: 4px 8px; }
"""


class SegmentDisplay(QWidget):
    """Табло из сегментов, как индикатор колонки."""

    def __init__(self, text: str) -> None:
        super().__init__()
        self._text = text
        self.setMinimumHeight(42)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_text(self, text: str) -> None:
        self._text = text
        self.setMinimumWidth(max(120, len(text) * 22))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(max(120, len(self._text) * 22), 42)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        on = QColor("#7dff67")
        off = QColor("#143322")
        cursor = 4
        top = 6
        for char in self._text:
            if char == ".":
                painter.fillRect(cursor, top + 28, 4, 4, on)
                cursor += 8
                continue
            mask = _SEGMENTS.get(char, _SEGMENTS[" "])
            _paint_digit(painter, cursor, top, mask, on, off)
            cursor += 20
        painter.end()


def _paint_digit(painter: QPainter, x: int, y: int, mask: str, on: QColor, off: QColor) -> None:
    thickness = 3
    width = 14
    height = 28
    bars = (
        (x + 2, y, width - 4, thickness),
        (x, y + 2, thickness, height // 2 - 3),
        (x + width - thickness, y + 2, thickness, height // 2 - 3),
        (x + 2, y + height // 2 - 1, width - 4, thickness),
        (x, y + height // 2 + 2, thickness, height // 2 - 4),
        (x + width - thickness, y + height // 2 + 2, thickness, height // 2 - 4),
        (x + 2, y + height - thickness, width - 4, thickness),
    )
    for lit, bar in zip(mask, bars, strict=True):
        painter.fillRect(*bar, on if lit == "1" else off)


class ColumnFace(QFrame):
    def __init__(self, pump_id: str) -> None:
        super().__init__()
        self.pump_id = pump_id
        self.setObjectName("column-face")
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(250, 228)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        head = QHBoxLayout()
        self.name = QLabel("КОЛОНКА")
        self.name.setObjectName("face-name")
        self.lamp = QLabel("")
        self.lamp.setFixedSize(14, 14)
        self.state = QLabel("НЕТ СВЯЗИ")
        self.state.setObjectName("face-state")
        self.state.setMinimumWidth(150)
        self.state.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.name)
        head.addStretch(1)
        head.addWidget(self.lamp)
        head.addWidget(self.state)
        layout.addLayout(head)
        display = QFrame()
        display.setObjectName("display")
        display_layout = QVBoxLayout(display)
        display_layout.setContentsMargins(8, 6, 8, 6)
        money_caption = QLabel("СУММА, тг")
        money_caption.setStyleSheet("color: #8fbf9a;")
        self.money = SegmentDisplay("000000")
        volume_caption = QLabel("ДОЗА, л")
        volume_caption.setStyleSheet("color: #8fbf9a;")
        self.volume = SegmentDisplay("0000.00")
        display_layout.addWidget(money_caption)
        display_layout.addWidget(self.money)
        display_layout.addWidget(volume_caption)
        display_layout.addWidget(self.volume)
        layout.addWidget(display)
        self.price = QLabel("цена —")
        self.price.setObjectName("face-meta")
        self.totalizer = QLabel("счётчик —")
        self.totalizer.setObjectName("face-total")
        layout.addWidget(self.price)
        layout.addWidget(self.totalizer)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.selected_callback(self.pump_id)
        super().mousePressEvent(event)

    def selected_callback(self, pump_id: str) -> None:
        del pump_id

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def show_pump(self, pump: PumpView, money: str, volume: str) -> None:
        caption = status_caption(pump.status, pump.observation_quality)
        self.name.setText(pump.name.upper())
        self.state.setText(caption)
        color = lamp_color(caption)
        self.lamp.setStyleSheet(f"background: {color}; border-radius: 7px;")
        self.money.set_text(money)
        self.volume.set_text(volume)
        self.price.setText(f"цена {pump.price_kzt_per_liter} тг/л")
        if pump.totalizer_ml is None:
            self.totalizer.setText("счётчик скрыт")
        else:
            whole = pump.totalizer_ml // 1000
            hundredths = (pump.totalizer_ml % 1000) // 10
            self.totalizer.setText(f"счётчик {whole:06d}.{hundredths:02d} л")


class ConsoleWindow(QMainWindow):
    def __init__(self, service: CashierService, env: str) -> None:
        super().__init__()
        self._service = service
        self._env = env
        self._faces: dict[str, ColumnFace] = {}
        self._order: list[str] = []
        self._selected: str | None = None
        self._buffer = ""
        self._mode = "money"
        self._memory: dict[str, tuple[int | None, int | None]] = {}
        self._addresses: dict[str, str] = {}
        self.setWindowTitle("AGZS — пульт колонок")
        self.resize(1080, 720)
        self.setStyleSheet(_STYLE)
        root = QWidget()
        root.setObjectName("console")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(self._build_header())
        if env != "production":
            banner = QLabel("Учебный пульт. Это не касса: расчёты с покупателем не проводятся, чек не печатается.")
            banner.setObjectName("training-banner")
            layout.addWidget(banner)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.grid_host)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(scroll, 1)
        layout.addWidget(self._build_deck())
        self.message = QLabel("Войдите, чтобы открыть смену и увидеть колонки.")
        self.message.setObjectName("message")
        layout.addWidget(self.message)
        if service.training:
            layout.addWidget(self._build_training())
        timer = QTimer(self)
        timer.timeout.connect(self._tick)
        timer.start(200)

    def _build_header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("header")
        row = QHBoxLayout(header)
        title = QLabel("ПУЛЬТ УПРАВЛЕНИЯ")
        title.setObjectName("title")
        note = QLabel("Колонки")
        note.setObjectName("header-note")
        self.login_name = QLineEdit()
        self.login_name.setObjectName("login-name")
        self.login_name.setPlaceholderText("Имя")
        self.login_name.setFixedWidth(120)
        self.login_password = QLineEdit()
        self.login_password.setObjectName("login-password")
        self.login_password.setPlaceholderText("Пароль")
        self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.login_password.setFixedWidth(120)
        enter = QPushButton("Вход")
        enter.setObjectName("login")
        enter.clicked.connect(self._login)
        self.shift = QLabel("Смена закрыта")
        self.shift.setObjectName("shift")
        close = QPushButton("Закрыть смену")
        close.setObjectName("quiet")
        close.clicked.connect(self._close_shift)
        journal = QPushButton("Журнал")
        journal.setObjectName("quiet")
        journal.clicked.connect(self._history)
        incidents = QPushButton("Инциденты")
        incidents.setObjectName("quiet")
        incidents.clicked.connect(self._incidents)
        self.clock = QLabel("--:--:--")
        self.clock.setObjectName("clock")
        row.addWidget(title)
        row.addWidget(note)
        row.addStretch(1)
        row.addWidget(self.login_name)
        row.addWidget(self.login_password)
        row.addWidget(enter)
        row.addWidget(self.shift)
        row.addWidget(close)
        row.addWidget(journal)
        row.addWidget(incidents)
        row.addWidget(self.clock)
        return header

    def _build_deck(self) -> QWidget:
        deck = QWidget()
        deck.setObjectName("deck")
        row = QHBoxLayout(deck)
        order = QVBoxLayout()
        caption = QLabel("ДОЗА")
        caption.setObjectName("face-name")
        self.dose_entry = QLabel("0")
        self.dose_entry.setObjectName("dose-entry")
        self.dose_entry.setMinimumWidth(220)
        self.dose_hint = QLabel("Режим: сумма, тенге")
        modes = QHBoxLayout()
        self._modes = QButtonGroup(self)
        money = QPushButton("Сумма")
        money.setObjectName("mode")
        money.setCheckable(True)
        money.setChecked(True)
        volume = QPushButton("Литры")
        volume.setObjectName("mode")
        volume.setCheckable(True)
        self._modes.addButton(money)
        self._modes.addButton(volume)
        money.clicked.connect(lambda: self._set_mode("money"))
        volume.clicked.connect(lambda: self._set_mode("volume"))
        modes.addWidget(money)
        modes.addWidget(volume)
        order.addWidget(caption)
        order.addWidget(self.dose_entry)
        order.addWidget(self.dose_hint)
        order.addLayout(modes)
        row.addLayout(order, 1)
        keys = QGridLayout()
        labels = ("7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "C")
        for index, label in enumerate(labels):
            button = QPushButton(label)
            button.setObjectName("key")
            button.clicked.connect(lambda _checked=False, value=label: self._press(value))
            if label == ".":
                self._dot = button
            keys.addWidget(button, index // 3, index % 3)
        self._dot.setEnabled(False)
        row.addLayout(keys)
        commands = QVBoxLayout()
        start = QPushButton("ПУСК")
        start.setObjectName("start")
        start.clicked.connect(self._start)
        stop = QPushButton("СТОП")
        stop.setObjectName("stop")
        stop.clicked.connect(self._stop)
        reset = QPushButton("Сброс дозы")
        reset.setObjectName("reset")
        reset.clicked.connect(self._reset)
        block = QPushButton("Блокировка пуска")
        block.setObjectName("block")
        block.clicked.connect(self._block)
        commands.addWidget(start)
        commands.addWidget(stop)
        commands.addWidget(reset)
        commands.addWidget(block)
        row.addLayout(commands)
        return deck

    def _build_training(self) -> QWidget:
        host = QWidget()
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        note = QLabel("Учебная модель звена")
        row.addWidget(note)
        for title, slot, name in (
            ("Обрыв звена", self._cut, "cut-link"),
            ("Восстановить звено", self._restore, "restore-link"),
        ):
            button = QPushButton(title)
            button.setObjectName(name)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        return host

    def _login(self) -> None:
        try:
            self._service.login(self.login_name.text().strip(), self.login_password.text())
            try:
                shift = self._service.open_shift()
            except Exception as exc:  # noqa: BLE001
                if "уже открыта" not in str(exc):
                    raise
                self.shift.setText("Смена открыта")
            else:
                self.shift.setText(f"Смена {shift.number}")
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            return
        self._say("Смена открыта. Выберите колонку и наберите дозу.")
        self._tick()

    def _close_shift(self) -> None:
        try:
            shift = self._service.close_shift()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            return
        self.shift.setText(f"Смена {shift.number} закрыта")
        self._say(f"Смена {shift.number} закрыта")

    def _tick(self) -> None:
        from datetime import datetime

        self.clock.setText(datetime.now().strftime("%H:%M:%S"))
        try:
            self._service.tick()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            return
        if self._service.actor_id is not None:
            self._refresh()

    def _refresh(self) -> None:
        try:
            pumps = self._service.pumps()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            return
        identifiers = [pump.pump_id for pump in pumps]
        if identifiers != self._order:
            self._rebuild(pumps)
        for pump in pumps:
            self._addresses[pump.pump_id] = pump.address
            remembered = self._memory.get(pump.pump_id, (None, None))
            money, volume, remembered_ml, remembered_kzt = face_digits(
                status=pump.status,
                observation_quality=pump.observation_quality,
                dose_ml=pump.dose_ml,
                provisional_kzt=pump.provisional_kzt,
                remembered_ml=remembered[0],
                remembered_kzt=remembered[1],
            )
            self._memory[pump.pump_id] = (remembered_ml, remembered_kzt)
            self._faces[pump.pump_id].show_pump(pump, money, volume)
            self._faces[pump.pump_id].set_selected(pump.pump_id == self._selected)
        if self._selected is None and pumps:
            self._select(pumps[0].pump_id)

    def _rebuild(self, pumps: list[PumpView]) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._faces.clear()
        self._order = [pump.pump_id for pump in pumps]
        for index, pump in enumerate(pumps):
            face = ColumnFace(pump.pump_id)
            face.selected_callback = self._select
            self._faces[pump.pump_id] = face
            self.grid.addWidget(face, index // 3, index % 3)

    def _select(self, pump_id: str) -> None:
        self._selected = pump_id
        for identifier, face in self._faces.items():
            face.set_selected(identifier == pump_id)
        self._show_buffer()

    def _selected_pump(self) -> PumpView | None:
        if self._selected is None:
            return None
        try:
            pumps = self._service.pumps()
        except Exception:  # noqa: BLE001
            return None
        for pump in pumps:
            if pump.pump_id == self._selected:
                return pump
        return None

    def _set_mode(self, mode: str) -> None:
        self._mode = mode
        self._buffer = ""
        self._dot.setEnabled(mode == "volume")
        self._show_buffer()

    def _press(self, key: str) -> None:
        if key == "C":
            self._buffer = ""
        elif key == ".":
            if self._mode != "volume" or "." in self._buffer:
                return
            self._buffer = (self._buffer or "0") + "."
        elif key.isdigit():
            if self._mode == "money" and len(self._buffer) >= 6:
                return
            if self._mode == "volume":
                frac = self._buffer.split(".")[1] if "." in self._buffer else ""
                if "." in self._buffer and len(frac) >= 2:
                    return
                if len(self._buffer) >= 7:
                    return
            self._buffer += key
        self._show_buffer()

    def _show_buffer(self) -> None:
        shown = self._buffer or "0"
        self.dose_entry.setText(shown)
        if self._mode == "money":
            self.dose_hint.setText("Режим: сумма, тенге")
            return
        pump = self._selected_pump()
        if pump is None or not self._buffer:
            self.dose_hint.setText("Режим: литры")
            return
        try:
            amount = preset_kzt(self._buffer, "volume", pump.price_kzt_per_liter)
        except ValueError:
            self.dose_hint.setText("Режим: литры")
            return
        self.dose_hint.setText(f"Режим: литры, к пуску {amount} тг")

    def _start(self) -> None:
        pump = self._selected_pump()
        if pump is None:
            self._say("Выберите колонку")
            return
        try:
            amount = preset_kzt(self._buffer, self._mode, pump.price_kzt_per_liter)
            sale = self._service.begin_prepay(pump.pump_id, amount)
            sale = self._service.take_payment(sale.sale_id)
            sale = self._service.open_dispense_gate(sale.sale_id)
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            self._refresh()
            return
        self._memory.pop(pump.pump_id, None)
        self._say(f"Пуск. {sale.message}")
        self._refresh()

    def _stop(self) -> None:
        pump = self._selected_pump()
        if pump is None:
            self._say("Выберите колонку")
            return
        if pump.status != "DISPENSING":
            self._say("Колонка не в наливе. Клапан на оборудовании закрывается аппаратной кнопкой.")
            return
        if self._service.training:
            try:
                self._service.training_stop_early(pump.address)
            except Exception as exc:  # noqa: BLE001
                self._say(str(exc))
                return
            self._say("Налив учебной модели остановлен.")
            self._refresh()
            return
        self._block()

    def _reset(self) -> None:
        self._buffer = ""
        if self._selected is not None:
            self._memory.pop(self._selected, None)
        self._show_buffer()
        self._refresh()
        self._say("Доза сброшена.")

    def _block(self) -> None:
        try:
            self._say(self._service.latch_commands())
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))

    def _history(self) -> None:
        try:
            rows = self._service.history()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            return
        text = "\n".join(f"{row.display_number}  {row.prepaid_kzt} тг  {row.order_status}" for row in rows) or "Пусто"
        QMessageBox.information(self, "Журнал", text)

    def _incidents(self) -> None:
        try:
            rows = self._service.incidents()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))
            return
        text = "\n".join(f"{row.code}: {row.message}" for row in rows) or "Пусто"
        QMessageBox.information(self, "Инциденты", text)

    def _cut(self) -> None:
        try:
            self._service.training_cut_link()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))

    def _restore(self) -> None:
        try:
            self._service.training_restore_link()
        except Exception as exc:  # noqa: BLE001
            self._say(str(exc))

    def _say(self, text: str) -> None:
        self.message.setText(text)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if isinstance(self.focusWidget(), QLineEdit):
            super().keyPressEvent(event)
            return
        symbol = event.text()
        if symbol.isdigit() or symbol in ".,":
            self._press("." if symbol == "," else symbol)
            return
        if event.key() == Qt.Key.Key_Backspace:
            self._buffer = self._buffer[:-1]
            self._show_buffer()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self._start()
            return
        if event.key() == Qt.Key.Key_Escape:
            self._press("C")
            return
        super().keyPressEvent(event)
