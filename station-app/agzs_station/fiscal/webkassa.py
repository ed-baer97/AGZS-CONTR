"""Порт Webkassa. Без контракта сдачи нет и успех не подменяется."""

from agzs_station.errors import IntegrationNotConfigured

_MESSAGE = "Webkassa не настроена"


class WebkassaPort:
    name = "webkassa"

    def submit(self, step_code: str, idempotency_key: str) -> None:
        del step_code, idempotency_key
        raise IntegrationNotConfigured(_MESSAGE)

    def reconcile(self, idempotency_key: str) -> None:
        del idempotency_key
        raise IntegrationNotConfigured(_MESSAGE)
