"""Ошибки каркаса. Они не подменяют отказ оборудования успешным результатом."""


class ConfigurationError(Exception):
    """Профиль собран недопустимым образом."""


class IntegrationNotConfigured(Exception):
    """Боевой адаптер вызван без утверждённого контракта и настроек."""


class ExchangeNotPermitted(Exception):
    """Обмен с АСКА-02 ещё не допущен."""


class EmulatorNotReady(Exception):
    """Виртуальные колонки подключаются на этапе 3 и не открывают оборудование."""
