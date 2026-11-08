"""Пульт колонок рисуется без экрана и принимает дозу."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from agzs_station.application.cashier import CashierService
from agzs_station.fiscal.training import TrainingNoReceiptPolicy
from agzs_station.payments.fake import FakePaymentPort
from agzs_station.ui.console import ConsoleWindow
from tests.integration.test_column_board import _stand


def test_console_starts_a_column_and_shows_its_dose(tmp_path) -> None:
    application = QApplication.instance() or QApplication([])
    _manager, _supervisor, adapter, _clock, factory = _stand(tmp_path)
    service = CashierService(
        factory,
        adapter,
        FakePaymentPort(),
        {"TRAINING_NO_RECEIPT": TrainingNoReceiptPolicy()},
        env="emulator",
    )
    window = ConsoleWindow(service, "emulator")
    window.resize(1080, 720)
    window.show()
    application.processEvents()
    window.login_name.setText("cashier")
    window.login_password.setText("training-password")
    window._login()
    assert window.shift.text().startswith("Смена")
    assert len(window._faces) == 2
    assert {face.state.text() for face in window._faces.values()} == {"СВОБОДНА"}
    for digit in "500":
        window._press(digit)
    assert window.dose_entry.text() == "500"
    window._start()
    adapter.advance_ms(500)
    window._tick()
    faces = list(window._faces.values())
    pouring = next(face for face in faces if face.state.text() == "НАЛИВ")
    assert pouring.volume._text == "0000.50"
    assert pouring.money._text == "000050"
    idle = next(face for face in faces if face is not pouring)
    assert idle.state.text() == "СВОБОДНА"
    image = window.grab()
    target = tmp_path / "console.png"
    assert image.save(str(target))
    assert target.stat().st_size > 5_000
    window.close()
    application.processEvents()
