import io
import logging
from pathlib import Path

from agzs_station.config import Settings
from agzs_station.logging_config import configure_logging


def test_password_is_redacted(tmp_path: Path) -> None:
    buffer = io.StringIO()
    settings = Settings(
        env="test",
        pump_driver="emulator",
        data_dir=tmp_path,
        log_dir=tmp_path,
        log_level="INFO",
        central_base_url="",
        database_url="",
        keyring_service_name="agzs-control",
    )
    configure_logging(settings, stream=buffer)
    logging.getLogger("agzs").info("login password=super-secret")
    printed = buffer.getvalue()
    stored = (tmp_path / "station.log").read_text(encoding="utf-8")
    assert "super-secret" not in printed
    assert "super-secret" not in stored
    assert "password=***" in printed
