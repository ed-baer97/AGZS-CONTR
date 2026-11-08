from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from agzs_shared.enums import LinkDriver, ObservationQuality, PumpStatus
from agzs_station.database.base import Base
from agzs_station.database.checks import one_of
from agzs_station.database.time import utc_now


class Link(Base):
    """Звено эмулятора или будущей АСКА-02.

    Управление АСКА-02 в базе выключено, пока отдельная миграция после
    акта допуска не снимет ограничение. Флаг сам по себе газ не пускает.
    """

    __tablename__ = "links"
    __table_args__ = (
        CheckConstraint("max_channels >= 0 AND max_channels <= 6", name="channel_capacity"),
        CheckConstraint(
            "driver = 'emulator' OR control_enabled = 0",
            name="aska_control_off",
        ),
        one_of("driver", LinkDriver, "driver"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    driver: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    max_channels: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    passive_read_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    control_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    protocol_id: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    firmware_version: Mapped[str] = mapped_column(
        String(128), nullable=False, default="", server_default=text("''")
    )
    config_json: Mapped[str] = mapped_column(
        Text, nullable=False, default="{}", server_default=text("'{}'")
    )


class Pump(Base):
    __tablename__ = "pumps"
    __table_args__ = (
        UniqueConstraint("link_id", "channel_no", name="uq_pumps_link_channel"),
        UniqueConstraint("link_id", "protocol_address", name="uq_pumps_link_address"),
        CheckConstraint("channel_no >= 1 AND channel_no <= 6", name="channel_no_range"),
        CheckConstraint("price_kzt_per_liter >= 0", name="price_nonnegative"),
        one_of("status", PumpStatus, "status"),
        one_of("observation_quality", ObservationQuality, "observation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    link_id: Mapped[str] = mapped_column(String(36), ForeignKey("links.id"), nullable=False)
    channel_no: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol_address: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    price_kzt_per_liter: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=PumpStatus.OFFLINE.value
    )
    observation_quality: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ObservationQuality.UNKNOWN.value
    )
    # Цикл со sales: внешний ключ добавляется отдельно, после создания обеих таблиц.
    active_sale_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("sales.id", use_alter=True, name="fk_pumps_active_sale_id_sales"),
    )
    maintenance_latched: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )
    status_changed_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)


class PumpEvent(Base):
    __tablename__ = "pump_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    pump_id: Mapped[str] = mapped_column(String(36), ForeignKey("pumps.id"), nullable=False)
    sale_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("sales.id"))
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(32))
    to_status: Mapped[str | None] = mapped_column(String(32))
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    occurred_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
