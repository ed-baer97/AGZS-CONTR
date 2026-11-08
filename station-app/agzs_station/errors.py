"""Ошибки каркаса. Они не подменяют отказ оборудования успешным результатом."""


class ConfigurationError(Exception):
    """Профиль собран недопустимым образом."""


class IntegrationNotConfigured(Exception):
    """Боевой адаптер вызван без утверждённого контракта и настроек."""


class ExchangeNotPermitted(Exception):
    """Обмен с АСКА-02 ещё не допущен."""


class UnknownPumpAddress(Exception):
    """В модели эмулятора нет колонки с таким адресом."""


class SaleBlocked(Exception):
    """Шлюз продажи закрыт. Это не отказ оборудования и не успешная оплата."""


class AccessDenied(Exception):
    """У роли нет права на действие."""


class LinkBusy(Exception):
    """На звене уже есть незавершённый обмен."""
