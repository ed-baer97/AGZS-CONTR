from sqlalchemy import Boolean, CheckConstraint, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from agzs_station.database.base import Base


class StationProfile(Base):
    """Одна касса — одна станция. Вторая строка невозможна."""

    __tablename__ = "station_profile"
    __table_args__ = (CheckConstraint("singleton = 1", name="one_station"),)

    singleton: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    station_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    config_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    sales_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    rounding_policy_id: Mapped[str | None] = mapped_column(String(64))
    fiscal_policy_id: Mapped[str | None] = mapped_column(String(64))
