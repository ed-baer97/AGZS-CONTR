"""Промышленная сборка. Учебные порты этим модулем не импортируются."""

from dataclasses import dataclass

from agzs_station.config import Settings
from agzs_station.errors import ConfigurationError
from agzs_station.fiscal.policy import DenyAllFiscalPolicy
from agzs_station.payments.kaspi import KaspiSmartPosPort
from agzs_station.pumps.adapters.aska02 import Aska02Adapter


@dataclass(frozen=True)
class AppContext:
    env: str
    driver_id: str
    payment_port_name: str
    fiscal_policy_id: str
    pump_control_enabled: bool
    pump: object
    payment: object
    fiscal: object


def wire(settings: Settings) -> AppContext:
    if settings.env != "production":
        raise ConfigurationError("Промышленная сборка запускается только при AGZS_ENV=production")
    if settings.pump_driver != "aska02":
        raise ConfigurationError("Промышленный профиль не использует эмулятор колонок")
    pump = Aska02Adapter()
    payment = KaspiSmartPosPort()
    fiscal = DenyAllFiscalPolicy()
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
