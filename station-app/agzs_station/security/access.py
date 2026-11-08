"""Проверка права на сценарии, а не на кнопку."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from agzs_station.database.models import RolePermission, UserRole
from agzs_station.errors import AccessDenied

SALE_CREATE = "sale.create"
SHIFT_MANAGE = "shift.manage"
PUMP_MAINTAIN = "pump.maintain"
STATION_SETTINGS = "station.settings"
PRICE_CHANGE = "price.change"
REPORT_READ = "report.read"
USER_MANAGE = "user.manage"
AUDIT_READ = "audit.read"

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "cashier": (SALE_CREATE, SHIFT_MANAGE),
    "senior_cashier": (SALE_CREATE, SHIFT_MANAGE, REPORT_READ),
    "station_manager": (
        SALE_CREATE,
        SHIFT_MANAGE,
        REPORT_READ,
        STATION_SETTINGS,
        PRICE_CHANGE,
        AUDIT_READ,
        USER_MANAGE,
    ),
    "technician": (PUMP_MAINTAIN, AUDIT_READ),
    "auditor": (REPORT_READ, AUDIT_READ),
}


def permissions_of(session: Session, user_id: str) -> set[str]:
    codes = session.scalars(
        select(RolePermission.permission_code)
        .join(UserRole, UserRole.role_code == RolePermission.role_code)
        .where(UserRole.user_id == user_id)
    ).all()
    return set(codes)


def require(session: Session, user_id: str | None, permission: str) -> None:
    if user_id is None or permission not in permissions_of(session, user_id):
        raise AccessDenied("Недостаточно прав")
