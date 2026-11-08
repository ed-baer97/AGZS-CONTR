from agzs_station.errors import IntegrationNotConfigured

_MESSAGE = "Kaspi Smart POS не настроен"


class KaspiSmartPosPort:
    name = "kaspi_smart_pos"

    def start(self, idempotency_key: str, amount_kzt: int) -> None:
        del idempotency_key, amount_kzt
        raise IntegrationNotConfigured(_MESSAGE)

    def query(self, idempotency_key: str) -> None:
        del idempotency_key
        raise IntegrationNotConfigured(_MESSAGE)

    def reverse(self, idempotency_key: str) -> None:
        del idempotency_key
        raise IntegrationNotConfigured(_MESSAGE)
