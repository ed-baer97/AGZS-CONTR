from dataclasses import dataclass
from enum import StrEnum


class PaymentOutcome(StrEnum):
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    NOT_SUPPORTED = "NOT_SUPPORTED"


@dataclass(frozen=True)
class PaymentReply:
    outcome: PaymentOutcome
    amount_kzt: int | None = None
    provider_reference: str | None = None
