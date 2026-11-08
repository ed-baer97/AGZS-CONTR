"""Первичное наполнение учебной станции. Промышленный профиль этот модуль не вызывает."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from agzs_shared.ids import new_id
from agzs_station.database.models import (
    Link,
    Permission,
    Pump,
    Role,
    RolePermission,
    StationProfile,
    User,
    UserRole,
)
from agzs_station.domain.settlement import TEST_HALF_UP
from agzs_station.security.access import ROLE_PERMISSIONS
from agzs_station.security.passwords import hash_password

_ROLE_NAMES = {
    "cashier": "Кассир",
    "senior_cashier": "Старший кассир",
    "station_manager": "Управляющий станции",
    "technician": "Техник",
    "auditor": "Аудитор",
}
_PERMISSION_NAMES = {
    "sale.create": "Создавать продажу и принимать учебную оплату",
    "shift.manage": "Открывать и закрывать смену",
    "pump.maintain": "Переводить колонку в обслуживание",
    "station.settings": "Менять настройки станции и коммерческую блокировку",
    "price.change": "Менять цену новых продаж",
    "report.read": "Смотреть историю операций",
    "user.manage": "Назначать роли на станции",
    "audit.read": "Читать журнал аудита",
}


def ensure_training_station(session: Session, password: str, *, pump_count: int = 2) -> StationProfile:
    install_access(session)
    existing = session.scalar(select(StationProfile).limit(1))
    if existing is not None:
        return existing
    station = StationProfile(
        station_id=new_id(),
        organization_id=new_id(),
        code="VIRT",
        name="Учебная АГЗС",
        timezone="Asia/Almaty",
        rounding_policy_id=TEST_HALF_UP,
        fiscal_policy_id="TRAINING_NO_RECEIPT",
    )
    session.add(station)
    session.flush()
    link = Link(
        id=new_id(),
        station_id=station.station_id,
        driver="emulator",
        name="Учебное звено",
        max_channels=pump_count,
        passive_read_enabled=True,
        control_enabled=True,
        config_json="{}",
    )
    session.add(link)
    session.flush()
    for number in range(1, pump_count + 1):
        session.add(
            Pump(
                id=new_id(),
                link_id=link.id,
                channel_no=number,
                protocol_address=str(number),
                name=f"Колонка {number}",
                price_kzt_per_liter=100,
                status="IDLE",
                observation_quality="UNKNOWN",
            )
        )
    user = User(
        id=new_id(),
        station_id=station.station_id,
        display_name="cashier",
        password_hash=hash_password(password),
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role_code="cashier"))
    session.flush()
    return station


def install_access(session: Session) -> None:
    for code, description in _PERMISSION_NAMES.items():
        if session.get(Permission, code) is None:
            session.add(Permission(code=code, description=description))
    for code, name in _ROLE_NAMES.items():
        if session.get(Role, code) is None:
            session.add(Role(code=code, name=name))
    session.flush()
    for role, permissions in ROLE_PERMISSIONS.items():
        for permission in permissions:
            if session.get(RolePermission, (role, permission)) is None:
                session.add(RolePermission(role_code=role, permission_code=permission))
    session.flush()
