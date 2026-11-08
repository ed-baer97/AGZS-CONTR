from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from agzs_shared.enums import IncidentSeverity, ShiftStatus
from agzs_station.database.base import Base
from agzs_station.database.checks import one_of
from agzs_station.database.time import utc_now


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("1")
    )
    roster_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class Role(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)


class Permission(Base):
    __tablename__ = "permissions"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    description: Mapped[str] = mapped_column(String(400), nullable=False)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_code: Mapped[str] = mapped_column(String(64), ForeignKey("roles.code"), primary_key=True)
    permission_code: Mapped[str] = mapped_column(
        String(64), ForeignKey("permissions.code"), primary_key=True
    )


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), primary_key=True)
    role_code: Mapped[str] = mapped_column(String(64), ForeignKey("roles.code"), primary_key=True)


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        one_of("status", ShiftStatus, "status"),
        CheckConstraint("opening_cash_kzt >= 0", name="opening_cash_nonnegative"),
        CheckConstraint(
            "closing_cash_kzt IS NULL OR closing_cash_kzt >= 0",
            name="closing_cash_nonnegative",
        ),
        Index(
            "uq_shifts_one_open",
            "station_id",
            unique=True,
            sqlite_where=text("status = 'OPEN'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    opened_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    closed_at: Mapped[str | None] = mapped_column(String(40))
    opening_cash_kzt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closing_cash_kzt: Mapped[int | None] = mapped_column(Integer)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    actor_user_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    object_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(36))
    result: Mapped[str] = mapped_column(String(64), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (one_of("severity", IncidentSeverity, "severity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    sale_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sales.id"))
    pump_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("pumps.id"))
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    acknowledged_at: Mapped[str | None] = mapped_column(String(40))
