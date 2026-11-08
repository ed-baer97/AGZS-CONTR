from agzs_station.composition_production import AppContext
from agzs_station.config import Settings
from agzs_station.errors import ConfigurationError


def build(settings: Settings) -> AppContext:
    if settings.env == "production":
        if settings.pump_driver == "emulator":
            raise ConfigurationError("Промышленный профиль не использует эмулятор колонок")
        from agzs_station.composition_production import wire

        return wire(settings)
    if settings.env in {"emulator", "test"}:
        from agzs_station.composition_emulator import wire

        return wire(settings)
    raise ConfigurationError("Неизвестный профиль кассы")
