"""Модель центра. Коммерческие строки несут organization_id."""

import os
from pathlib import Path

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE")


class OfficeUser(Base):
    __tablename__ = "office_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    username: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    platform_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Membership(Base):
    __tablename__ = "memberships"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role_code: Mapped[str] = mapped_column(String(64), nullable=False)


class Station(Base):
    __tablename__ = "stations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    sales_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rounding_policy_id: Mapped[str | None] = mapped_column(String(64))
    fiscal_policy_id: Mapped[str | None] = mapped_column(String(64))


class PumpRecord(Base):
    __tablename__ = "pumps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price_kzt_per_liter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OFFLINE")
    observation_quality: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")


class PriceVersion(Base):
    __tablename__ = "price_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False)
    pump_id: Mapped[str] = mapped_column(String(36), nullable=False)
    price_kzt_per_liter: Mapped[int] = mapped_column(Integer, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class ShiftRecord(Base):
    __tablename__ = "shifts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[str | None] = mapped_column(String(36))


class SaleRecord(Base):
    __tablename__ = "sales"
    __table_args__ = (UniqueConstraint("station_id", "display_number", name="uq_sales_station_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    shift_id: Mapped[str | None] = mapped_column(String(36))
    pump_id: Mapped[str | None] = mapped_column(String(36))
    display_number: Mapped[str] = mapped_column(String(64), nullable=False)
    order_status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    payment_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    fiscal_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNCONFIGURED")
    dispensing_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    settlement_status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_READY")
    prepaid_kzt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unit_price_kzt_per_liter: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    delivered_ml: Mapped[int | None] = mapped_column(Integer)
    actual_kzt: Mapped[int | None] = mapped_column(Integer)
    delta_kzt: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class SyncInbox(Base):
    __tablename__ = "sync_inbox"

    station_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    body_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    station_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    received_at: Mapped[str] = mapped_column(String(40), nullable=False)


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)
    used_at: Mapped[str | None] = mapped_column(String(40))


class Heartbeat(Base):
    __tablename__ = "heartbeats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    received_at: Mapped[str] = mapped_column(String(40), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class ConfigChange(Base):
    __tablename__ = "config_changes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    document_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False)


class OfficeSession(Base):
    __tablename__ = "office_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    expires_at: Mapped[str] = mapped_column(String(40), nullable=False)


class AuditRecord(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    station_id: Mapped[str | None] = mapped_column(String(36))
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


COMMERCIAL_TABLES = (
    "stations",
    "pumps",
    "price_versions",
    "shifts",
    "sales",
    "sync_inbox",
    "heartbeats",
    "config_changes",
    "audit_log",
)


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        Path("data").mkdir(exist_ok=True)
    engine = create_engine(url, future=True)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def prepare(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        enable_row_level_security(engine)


def enable_row_level_security(engine: Engine) -> None:
    with engine.begin() as connection:
        for table in COMMERCIAL_TABLES:
            connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            connection.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            connection.execute(text(f"DROP POLICY IF EXISTS {table}_org ON {table}"))
            connection.execute(
                text(
                    f"""
                    CREATE POLICY {table}_org ON {table}
                    USING (
                        current_setting('agzs.platform', true) = 'on'
                        OR organization_id = current_setting('agzs.organization_id', true)
                    )
                    WITH CHECK (
                        current_setting('agzs.platform', true) = 'on'
                        OR organization_id = current_setting('agzs.organization_id', true)
                    )
                    """
                )
            )


def bind_tenant(session: Session, organization_id: str | None, *, platform: bool) -> None:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        return
    session.execute(
        text("SELECT set_config('agzs.organization_id', :org, true)"),
        {"org": organization_id or ""},
    )
    session.execute(
        text("SELECT set_config('agzs.platform', :flag, true)"),
        {"flag": "on" if platform else "off"},
    )


def default_database_url() -> str:
    return os.environ.get("CENTRAL_DATABASE_URL", "sqlite:///./data/central.db")
