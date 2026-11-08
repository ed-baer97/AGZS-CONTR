"""Настройки только из окружения. Адреса и секреты в код не вшиваются."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    env: str = Field(default="production", alias="AGZS_ENV")
    data_dir: Path = Field(default=Path("data"), alias="AGZS_DATA_DIR")
    log_dir: Path = Field(default=Path("logs"), alias="AGZS_LOG_DIR")
    log_level: str = Field(default="INFO", alias="AGZS_LOG_LEVEL")
    central_base_url: str = Field(default="", alias="AGZS_CENTRAL_BASE_URL")
    pump_driver: str = Field(default="aska02", alias="AGZS_PUMP_DRIVER")
    database_url: str = Field(default="", alias="AGZS_DATABASE_URL")
    keyring_service_name: str = Field(default="agzs-control", alias="AGZS_KEYRING_SERVICE_NAME")
    bootstrap_password: str = Field(default="", alias="AGZS_BOOTSTRAP_PASSWORD")

    @field_validator("env", mode="before")
    @classmethod
    def normalize_env(cls, value: object) -> str:
        text = _blank(value, "production")
        if text not in {"production", "emulator", "test"}:
            raise ValueError("AGZS_ENV должен быть production, emulator или test")
        return text

    @field_validator("pump_driver", mode="before")
    @classmethod
    def normalize_driver(cls, value: object) -> str:
        text = _blank(value, "aska02")
        if text not in {"emulator", "aska02"}:
            raise ValueError("AGZS_PUMP_DRIVER должен быть emulator или aska02")
        return text

    @field_validator("data_dir", mode="before")
    @classmethod
    def normalize_data_dir(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return Path("data")
        return value

    @field_validator("log_dir", mode="before")
    @classmethod
    def normalize_log_dir(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return Path("logs")
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def normalize_level(cls, value: object) -> str:
        text = _blank(value, "INFO").upper()
        if text not in _LEVELS:
            raise ValueError("AGZS_LOG_LEVEL должен быть уровнем logging")
        return text

    @field_validator(
        "central_base_url",
        "database_url",
        "keyring_service_name",
        "bootstrap_password",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("keyring_service_name")
    @classmethod
    def keyring_default(cls, value: str) -> str:
        return value or "agzs-control"

    def sqlite_url(self) -> str:
        if self.database_url:
            return self.database_url
        return "sqlite:///" + (self.data_dir / "station.db").as_posix()


def _blank(value: object, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default

