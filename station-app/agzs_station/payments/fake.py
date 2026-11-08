"""Тестовый порт. Его импортирует только учебная сборка, и он не проводит списание."""

from agzs_station.errors import IntegrationNotConfigured


class FakePaymentPort:
    name = "fake"

    def start(self, idempotency_key: str, amount_kzt: int) -> None:
        del idempotency_key, amount_kzt
        raise IntegrationNotConfigured("Тестовый сценарий оплаты не задан")

    def query(self, idempotency_key: str) -> None:
        del idempotency_key
        raise IntegrationNotConfigured("Тестовый сценарий оплаты не задан")

    def reverse(self, idempotency_key: str) -> None:
        del idempotency_key
        raise IntegrationNotConfigured("Тестовый сценарий оплаты не задан")
