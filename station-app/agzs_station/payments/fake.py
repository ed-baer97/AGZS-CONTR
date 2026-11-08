"""Учебный терминал. Его импортирует только сборка emulator и test.

Подтверждение остаётся внутри процесса и не является ответом Kaspi.
"""

from agzs_station.payments.port import PaymentOutcome, PaymentReply


class FakePaymentPort:
    name = "training"

    def __init__(self) -> None:
        self._answers: dict[str, PaymentReply] = {}

    def start(self, idempotency_key: str, amount_kzt: int) -> PaymentReply:
        known = self._answers.get(idempotency_key)
        if known is not None:
            return known
        reply = PaymentReply(
            outcome=PaymentOutcome.CONFIRMED,
            amount_kzt=amount_kzt,
            provider_reference="training",
        )
        self._answers[idempotency_key] = reply
        return reply

    def query(self, idempotency_key: str) -> PaymentReply:
        known = self._answers.get(idempotency_key)
        if known is None:
            return PaymentReply(outcome=PaymentOutcome.UNKNOWN)
        return known

    def reverse(self, idempotency_key: str) -> PaymentReply:
        del idempotency_key
        return PaymentReply(outcome=PaymentOutcome.NOT_SUPPORTED)
