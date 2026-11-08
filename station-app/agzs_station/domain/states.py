"""Допустимые переходы колонки. Вне схемы переход отклоняется."""

from agzs_shared.enums import AuthorizationStatus, PaymentStatus, PumpStatus

ALLOWED: frozenset[tuple[str, str]] = frozenset(
    {
        (PumpStatus.OFFLINE, PumpStatus.IDLE),
        (PumpStatus.OFFLINE, PumpStatus.RESERVED),
        (PumpStatus.OFFLINE, PumpStatus.PAYMENT_PENDING),
        (PumpStatus.OFFLINE, PumpStatus.DISPENSING),
        (PumpStatus.OFFLINE, PumpStatus.STOPPED),
        (PumpStatus.OFFLINE, PumpStatus.COMPLETED),
        (PumpStatus.OFFLINE, PumpStatus.ERROR),
        (PumpStatus.OFFLINE, PumpStatus.MAINTENANCE),
        (PumpStatus.IDLE, PumpStatus.RESERVED),
        (PumpStatus.IDLE, PumpStatus.MAINTENANCE),
        (PumpStatus.IDLE, PumpStatus.OFFLINE),
        (PumpStatus.RESERVED, PumpStatus.IDLE),
        (PumpStatus.RESERVED, PumpStatus.PAYMENT_PENDING),
        (PumpStatus.RESERVED, PumpStatus.OFFLINE),
        (PumpStatus.PAYMENT_PENDING, PumpStatus.IDLE),
        (PumpStatus.PAYMENT_PENDING, PumpStatus.READY),
        (PumpStatus.PAYMENT_PENDING, PumpStatus.OFFLINE),
        (PumpStatus.READY, PumpStatus.DISPENSING),
        (PumpStatus.READY, PumpStatus.ERROR),
        (PumpStatus.READY, PumpStatus.OFFLINE),
        (PumpStatus.DISPENSING, PumpStatus.COMPLETED),
        (PumpStatus.DISPENSING, PumpStatus.STOPPED),
        (PumpStatus.DISPENSING, PumpStatus.ERROR),
        (PumpStatus.DISPENSING, PumpStatus.OFFLINE),
        (PumpStatus.COMPLETED, PumpStatus.IDLE),
        (PumpStatus.COMPLETED, PumpStatus.ERROR),
        (PumpStatus.STOPPED, PumpStatus.IDLE),
        (PumpStatus.STOPPED, PumpStatus.ERROR),
        (PumpStatus.ERROR, PumpStatus.MAINTENANCE),
        (PumpStatus.ERROR, PumpStatus.OFFLINE),
        (PumpStatus.MAINTENANCE, PumpStatus.IDLE),
        (PumpStatus.MAINTENANCE, PumpStatus.OFFLINE),
    }
)


def can_transition(current: str, target: str) -> bool:
    if current == target:
        return True
    return (current, target) in ALLOWED


def restored_status(
    *,
    maintenance_latched: bool,
    has_sale: bool,
    payment_status: str | None,
    authorization_status: str | None,
    physical_status: str,
) -> str:
    """Куда вернуть колонку после подтверждённого чтения. Новый допуск отсюда не шлётся."""

    if physical_status == PumpStatus.ERROR:
        return PumpStatus.ERROR
    if physical_status == PumpStatus.DISPENSING and authorization_status == AuthorizationStatus.ACKNOWLEDGED:
        return PumpStatus.DISPENSING
    if physical_status in {PumpStatus.COMPLETED, PumpStatus.STOPPED}:
        if authorization_status == AuthorizationStatus.ACKNOWLEDGED:
            return physical_status
    if not has_sale and maintenance_latched:
        return PumpStatus.MAINTENANCE
    if not has_sale:
        return PumpStatus.IDLE
    if authorization_status == AuthorizationStatus.ACKNOWLEDGED:
        return PumpStatus.STOPPED
    if payment_status == PaymentStatus.CONFIRMED:
        return PumpStatus.PAYMENT_PENDING
    return PumpStatus.RESERVED
