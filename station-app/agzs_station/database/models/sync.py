from sqlalchemy import ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from agzs_shared.enums import SyncEventType
from agzs_station.database.base import Base
from agzs_station.database.checks import one_of
from agzs_station.database.time import utc_now


class OutboxEvent(Base):
    __tablename__ = "outbox"
    __table_args__ = (
        one_of("event_type", SyncEventType, "event_type"),
        Index("ix_outbox_pending", "published_at", "next_attempt_at"),
    )

    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(36), nullable=False)
    station_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("station_profile.station_id"), nullable=False
    )
    station_sequence: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, default=utc_now)
    published_at: Mapped[str | None] = mapped_column(String(40))
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    next_attempt_at: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(Text)


class SyncState(Base):
    __tablename__ = "sync_state"

    stream: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_acked_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_applied_config_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
