"""Окно кассира. Оно вызывает сценарии и не импортирует адаптеры колонок."""

import sys

from agzs_station.application.cashier import CashierService


def run_window(service: CashierService, env: str) -> int:
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import (
            QApplication,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QLineEdit,
            QListWidget,
            QMainWindow,
            QMessageBox,
            QPushButton,
            QVBoxLayout,
            QWidget,
        )
    except ImportError:
        print("Для окна нужен пакет PySide6: pip install 'agzs-station[ui]'", file=sys.stderr)
        return 2

    class Till(QMainWindow):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("AGZS Control")
            self.resize(860, 560)
            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)
            if env != "production":
                banner = QLabel("Учебный режим. Это не касса, расчёты с покупателем не проводятся.")
                banner.setObjectName("training-banner")
                layout.addWidget(banner)
            self.login_name = QLineEdit()
            self.login_name.setObjectName("login-name")
            self.login_password = QLineEdit()
            self.login_password.setEchoMode(QLineEdit.EchoMode.Password)
            self.login_password.setObjectName("login-password")
            form = QFormLayout()
            form.addRow("Имя", self.login_name)
            form.addRow("Пароль", self.login_password)
            layout.addLayout(form)
            enter = QPushButton("Войти")
            enter.setObjectName("login")
            enter.clicked.connect(self._login)
            layout.addWidget(enter)
            self.pumps = QListWidget()
            self.pumps.setObjectName("pumps")
            layout.addWidget(self.pumps)
            self.amount = QLineEdit()
            self.amount.setObjectName("amount")
            self.amount.setPlaceholderText("Сумма, тенге")
            layout.addWidget(self.amount)
            self.message = QLabel("")
            self.message.setObjectName("message")
            layout.addWidget(self.message)
            actions = QHBoxLayout()
            for title, slot, name in (
                ("Начать", self._begin, "begin"),
                ("Оплатить", self._pay, "pay"),
                ("Открыть отпуск", self._gate, "gate"),
                ("Запретить новые команды", self._latch, "latch"),
                ("Смена", self._shift, "shift"),
                ("История", self._history, "history"),
                ("Инциденты", self._incidents, "incidents"),
            ):
                button = QPushButton(title)
                button.setObjectName(name)
                button.clicked.connect(slot)
                actions.addWidget(button)
            layout.addLayout(actions)
            if service.training:
                training = QHBoxLayout()
                for title, slot, name in (
                    ("Обрыв звена", self._cut, "cut-link"),
                    ("Восстановить звено", self._restore, "restore-link"),
                    ("Ранний останов модели", self._early, "early-stop"),
                ):
                    button = QPushButton(title)
                    button.setObjectName(name)
                    button.clicked.connect(slot)
                    training.addWidget(button)
                layout.addLayout(training)
            self._sale_id: str | None = None
            self._addresses: dict[str, str] = {}
            timer = QTimer(self)
            timer.timeout.connect(self._tick)
            timer.start(200)

        def _login(self) -> None:
            try:
                service.login(self.login_name.text().strip(), self.login_password.text())
                service.open_shift()
                self._refresh()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))

        def _refresh(self) -> None:
            self.pumps.clear()
            self._addresses.clear()
            try:
                for pump in service.pumps():
                    self._addresses[pump.pump_id] = pump.address
                    dose = "" if pump.dose_ml is None else f", {pump.dose_ml} мл"
                    provisional = "" if pump.provisional_kzt is None else f", примерно {pump.provisional_kzt} тг"
                    self.pumps.addItem(
                        f"{pump.pump_id}|{pump.name}: {pump.status}/{pump.observation_quality}, "
                        f"{pump.price_kzt_per_liter} тг/л{dose}{provisional}"
                    )
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))

        def _selected_pump(self) -> str | None:
            item = self.pumps.currentItem()
            if item is None:
                return None
            return item.text().split("|", 1)[0]

        def _begin(self) -> None:
            pump_id = self._selected_pump()
            if pump_id is None or not self.amount.text().strip():
                self.message.setText("Выберите колонку и сумму")
                return
            try:
                sale = service.begin_prepay(pump_id, int(self.amount.text().strip()))
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            self._sale_id = sale.sale_id
            self.message.setText(f"{sale.display_number}. {sale.message}")
            self._refresh()

        def _pay(self) -> None:
            if self._sale_id is None:
                return
            try:
                sale = service.take_payment(self._sale_id)
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            self.message.setText(sale.message)
            self._refresh()

        def _gate(self) -> None:
            if self._sale_id is None:
                return
            try:
                sale = service.open_dispense_gate(self._sale_id)
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            self.message.setText(sale.message)
            self._refresh()

        def _latch(self) -> None:
            try:
                self.message.setText(service.latch_commands())
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))

        def _shift(self) -> None:
            try:
                shift = service.close_shift()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            self.message.setText(f"Смена {shift.number} закрыта")

        def _history(self) -> None:
            try:
                rows = service.history()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            QMessageBox.information(
                self,
                "История",
                "\n".join(f"{row.display_number} {row.order_status} {row.prepaid_kzt} тг" for row in rows) or "Пусто",
            )

        def _incidents(self) -> None:
            try:
                rows = service.incidents()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            QMessageBox.information(
                self,
                "Инциденты",
                "\n".join(f"{row.code}: {row.message}" for row in rows) or "Пусто",
            )

        def _tick(self) -> None:
            try:
                service.tick()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))
                return
            if service.actor_id is not None:
                self._refresh()

        def _cut(self) -> None:
            try:
                service.training_cut_link()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))

        def _restore(self) -> None:
            try:
                service.training_restore_link()
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))

        def _early(self) -> None:
            pump_id = self._selected_pump()
            address = None if pump_id is None else self._addresses.get(pump_id)
            if address is None:
                return
            try:
                service.training_stop_early(address)
            except Exception as exc:  # noqa: BLE001
                self.message.setText(str(exc))

    application = QApplication.instance() or QApplication([])
    window = Till()
    window.show()
    return application.exec()
