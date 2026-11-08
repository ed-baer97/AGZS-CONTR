"""Окно пульта. Оно вызывает сценарии и не импортирует адаптеры колонок."""

import sys

from agzs_station.application.cashier import CashierService


def run_window(service: CashierService, env: str) -> int:
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Для окна нужен пакет PySide6: pip install 'agzs-station[ui]'", file=sys.stderr)
        return 2
    from PySide6.QtGui import QFont, QFontDatabase

    from agzs_station.ui.console import ConsoleWindow

    application = QApplication.instance() or QApplication([])
    if "Segoe UI" in QFontDatabase.families():
        application.setFont(QFont("Segoe UI", 10))
    window = ConsoleWindow(service, env)
    window.show()
    return application.exec()
