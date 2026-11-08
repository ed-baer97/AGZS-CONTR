"""Общие перечисления. Касса и сервер импортируют их отсюда, а не копируют."""

from enum import StrEnum


class PumpStatus(StrEnum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    RESERVED = "RESERVED"
    PAYMENT_PENDING = "PAYMENT_PENDING"
    READY = "READY"
    DISPENSING = "DISPENSING"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    MAINTENANCE = "MAINTENANCE"


class ObservationQuality(StrEnum):
    CONFIRMED = "CONFIRMED"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class OrderStatus(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class PaymentStatus(StrEnum):
    NONE = "NONE"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"
    REVERSAL_PENDING = "REVERSAL_PENDING"
    REVERSED = "REVERSED"


class FiscalStatus(StrEnum):
    UNCONFIGURED = "UNCONFIGURED"
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class DispensingStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    AUTHORIZED = "AUTHORIZED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class SettlementStatus(StrEnum):
    NOT_READY = "NOT_READY"
    BALANCED = "BALANCED"
    ADJUSTMENT_REQUIRED = "ADJUSTMENT_REQUIRED"
    REFUND_PENDING = "REFUND_PENDING"
    REFUND_COMPLETED = "REFUND_COMPLETED"


class SyncStatus(StrEnum):
    NOT_QUEUED = "NOT_QUEUED"
    PENDING = "PENDING"
    ACKED = "ACKED"
    REJECTED = "REJECTED"


class ShiftStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class PaymentAttemptStatus(StrEnum):
    CREATED = "CREATED"
    SENT = "SENT"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class FiscalDocumentStatus(StrEnum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    ISSUED = "ISSUED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"


class AuthorizationStatus(StrEnum):
    CREATED = "CREATED"
    SENT = "SENT"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class DispenseEndReason(StrEnum):
    COMPLETED = "COMPLETED"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    UNKNOWN = "UNKNOWN"


class RefundStatus(StrEnum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class LinkDriver(StrEnum):
    EMULATOR = "emulator"
    ASKA02 = "aska02"


class IncidentSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class SyncEventType(StrEnum):
    SHIFT_OPENED = "shift.opened"
    SHIFT_CLOSED = "shift.closed"
    SALE_CREATED = "sale.created"
    PAYMENT_UPDATED = "payment.updated"
    FISCAL_UPDATED = "fiscal.updated"
    DISPENSING_AUTHORIZED = "dispensing.authorized"
    DISPENSING_FINISHED = "dispensing.finished"
    SALE_CLOSED = "sale.closed"
    REFUND_UPDATED = "refund.updated"
    AUDIT_RECORDED = "audit.recorded"
    PUMP_FAULT = "pump.fault"
