"""Учебная сборка. В промышленный профиль этот модуль не входит."""

from agzs_station.composition_production import AppContext
from agzs_station.config import Settings
from agzs_station.errors import ConfigurationError
from agzs_station.fiscal.training import TrainingNoReceiptPolicy
from agzs_station.payments.fake import FakePaymentPort
from agzs_station.pumps.adapters.emulator import EmulatorAdapter


def wire(settings: Settings) -> AppContext:
    if settings.env not in {"emulator", "test"}:
        raise ConfigurationError("Учебная сборка не запускается в промышленном профиле")
    if settings.pump_driver != "emulator":
        raise ConfigurationError("Профили emulator и test работают только с драйвером emulator")
    pump = EmulatorAdapter(pump_count=2, flow_ml_per_s=1000, price_kzt_per_liter=100)
    payment = FakePaymentPort()
    fiscal = TrainingNoReceiptPolicy()
    return AppContext(
        env=settings.env,
        driver_id=pump.driver_id,
        payment_port_name=payment.name,
        fiscal_policy_id=fiscal.policy_id,
        pump_control_enabled=pump.supports_control,
        pump=pump,
        payment=payment,
        fiscal=fiscal,
    )
