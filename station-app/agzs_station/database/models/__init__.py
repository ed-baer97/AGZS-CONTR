"""Импорт регистрирует таблицы в metadata до миграции и тестов."""

from agzs_station.database.models.equipment import Link, Pump, PumpEvent
from agzs_station.database.models.people import (
    AuditLog,
    Incident,
    Permission,
    Role,
    RolePermission,
    Shift,
    User,
    UserRole,
)
from agzs_station.database.models.sales import (
    DispensingAuthorization,
    DispensingResult,
    FiscalDocument,
    NumberSequence,
    PaymentAttempt,
    Refund,
    Sale,
    SaleEvent,
)
from agzs_station.database.models.station import StationProfile
from agzs_station.database.models.sync import OutboxEvent, Setting, SyncState

__all__ = [
    "AuditLog",
    "DispensingAuthorization",
    "DispensingResult",
    "FiscalDocument",
    "Incident",
    "Link",
    "NumberSequence",
    "OutboxEvent",
    "PaymentAttempt",
    "Permission",
    "Pump",
    "PumpEvent",
    "Refund",
    "Role",
    "RolePermission",
    "Sale",
    "SaleEvent",
    "Setting",
    "Shift",
    "StationProfile",
    "SyncState",
    "User",
    "UserRole",
]
