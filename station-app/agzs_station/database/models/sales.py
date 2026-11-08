from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from agzs_shared.enums import (
    AuthorizationStatus,
    DispenseEndReason,
    DispensingStatus,
    FiscalDocumentStatus,
    FiscalStatus,
    OrderStatus,
    PaymentAttemptStatus,
    PaymentStatus,
    RefundStatus,
    SettlementStatus,
    SyncStatus,
)
from agzs_station.database.base import Base
from agzs_station.database.checks import one_of
from agzs_station.database.time import utc_now


class NumberSequence(Base):
    __tablename__ = "number_sequences"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, nullable=False)


class Sale(Base):
    __tablename__ = "sales"
    __table_args__ = (
        one_of("order_status", OrderStatus, "order_status"),
        one_of("payment_status", PaymentStatus, "payment_status"),
        one_of("fiscal_status", FiscalStatus, "fiscal_status"),
        one_of("dispensing_status", DispensingStatus, "dispensing_status"),
        one_of("settlement_status", SettlementStatus, "settlement_status"),
        one_of("sync_status", SyncStatus, "sync_status"),
        CheckConstraint("prepaid_kzt >= 0", name="prepaid_nonnegative"),
        CheckConstraint("unit_price_kzt_per_liter >= 0", name="unit_price_nonnegative"),
        CheckConstraint("delivered_ml IS NULL OR delivered_ml >= 0", name="delivered_nonnegative"),
        CheckConstraint("actual_kzt IS NULL OR actual_kzt >= 0", name="actual_nonnegative"),
        CheckConstraint("currency = 'KZT'", name="currency_kzt"),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_sales_shift_created", "shift_id", "created_at"),
        Index("ix_sales_pump_order", "pump_id", "order_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    shift_id: Mapped[str] = mapped_column(String(36), ForeignKey("shifts.id"), nullable=False)
    pump_id: Mapped[str] = mapped_column(String(36), ForeignKey("pumps.id"), nullable=False)
    display_number: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    order_status: Mapped[str] = mapped_column(String(32), nullable=False)
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    fiscal_status: Mapped[str] = mapped_column(String(32), nullable=False)
    dispensing_status: Mapped[str] = mapped_column(String(32), nullable=False)
    settlement_status: Mapped[str] = mapped_column(String(32), nullable=False)
    sync_status: Mapped[str] = mapped_column(String(32), nullable=False)
    prepaid_kzt: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_kzt_per_liter: Mapped[int] = mapped_column(Integer, nullable=False)
    rounding_policy_id: Mapped[str | None] = mapped_column(String(64))
    delivered_ml: Mapped[int | None] = mapped_column(Integer)
    actual_kzt: Mapped[int | None] = mapped_column(Integer)
    delta_kzt: Mapped[int | None] = mapped_column(Integer)
    fiscal_policy_id: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="KZT", server_default=text("'KZT'")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    closed_at: Mapped[str | None] = mapped_column(String(40))


class SaleEvent(Base):
    __tablename__ = "sale_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    axis: Mapped[str] = mapped_column(String(32), nullable=False)
    from_value: Mapped[str | None] = mapped_column(String(64))
    to_value: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    payment_attempt_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("payment_attempts.id")
    )
    fiscal_document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("fiscal_documents.id")
    )
    authorization_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("dispensing_authorizations.id")
    )
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("sale_id", "attempt_no", name="uq_payment_attempts_sale_no"),
        one_of("status", PaymentAttemptStatus, "status"),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint("amount_kzt >= 0", name="amount_nonnegative"),
        Index(
            "uq_payment_attempts_one_confirmed",
            "sale_id",
            unique=True,
            sqlite_where=text("status = 'CONFIRMED'"),
        ),
        Index("ix_payment_attempts_provider_reference", "provider_reference"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_kzt: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_reference: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    updated_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)


class FiscalDocument(Base):
    __tablename__ = "fiscal_documents"
    __table_args__ = (
        one_of("status", FiscalDocumentStatus, "status"),
        Index(
            "uq_fiscal_documents_open_step",
            "sale_id",
            "step_code",
            unique=True,
            sqlite_where=text("status IN ('SUBMITTING', 'ISSUED', 'UNKNOWN')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    step_code: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_document_id: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)


class DispensingAuthorization(Base):
    __tablename__ = "dispensing_authorizations"
    __table_args__ = (
        UniqueConstraint("sale_id", "attempt_no", name="uq_dispensing_authorizations_sale_no"),
        one_of("status", AuthorizationStatus, "status"),
        CheckConstraint("attempt_no >= 1", name="attempt_no_positive"),
        CheckConstraint("authorized_kzt >= 0", name="authorized_nonnegative"),
        CheckConstraint(
            "totalizer_start_ml IS NULL OR totalizer_start_ml >= 0",
            name="totalizer_start_nonnegative",
        ),
        Index(
            "uq_dispensing_authorizations_one_ack",
            "sale_id",
            unique=True,
            sqlite_where=text("status = 'ACKNOWLEDGED'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    pump_id: Mapped[str] = mapped_column(String(36), ForeignKey("pumps.id"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    authorized_kzt: Mapped[int] = mapped_column(Integer, nullable=False)
    totalizer_start_ml: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)


class DispensingResult(Base):
    __tablename__ = "dispensing_results"
    __table_args__ = (
        one_of("end_reason", DispenseEndReason, "end_reason"),
        CheckConstraint("totalizer_end_ml >= 0", name="totalizer_end_nonnegative"),
        CheckConstraint("delivered_ml >= 0", name="delivered_nonnegative"),
        CheckConstraint("actual_kzt >= 0", name="actual_nonnegative"),
    )

    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    authorization_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("dispensing_authorizations.id"), nullable=False
    )
    totalizer_end_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    delivered_ml: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_kzt: Mapped[int] = mapped_column(Integer, nullable=False)
    end_reason: Mapped[str] = mapped_column(String(32), nullable=False)
    finished_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Refund(Base):
    __tablename__ = "refunds"
    __table_args__ = (
        one_of("status", RefundStatus, "status"),
        CheckConstraint("amount_kzt >= 0", name="amount_nonnegative"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    sale_id: Mapped[str] = mapped_column(String(36), ForeignKey("sales.id"), nullable=False)
    amount_kzt: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    fiscal_policy_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
