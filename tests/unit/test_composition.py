import os
import subprocess
import sys
from pathlib import Path

import pytest

from agzs_station.composition import build
from agzs_station.config import Settings
from agzs_station.errors import ConfigurationError, ExchangeNotPermitted, IntegrationNotConfigured
from agzs_station.fiscal.policy import DenyAllFiscalPolicy
from agzs_station.payments.kaspi import KaspiSmartPosPort
from agzs_station.pumps.adapters.aska02 import Aska02Adapter

ROOT = Path(__file__).resolve().parents[2]


def _settings(tmp_path: Path, **overrides: str) -> Settings:
    values: dict[str, object] = {
        "env": "production",
        "pump_driver": "aska02",
        "data_dir": tmp_path,
        "log_dir": tmp_path,
        "log_level": "INFO",
        "central_base_url": "",
        "database_url": "",
        "keyring_service_name": "agzs-control",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_refuses_emulator(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError):
        build(_settings(tmp_path, env="production", pump_driver="emulator"))


def test_aska_and_kaspi_fail_closed() -> None:
    with pytest.raises(ExchangeNotPermitted):
        Aska02Adapter().authorize("1", "cmd", 100)
    assert Aska02Adapter().supports_control is False
    with pytest.raises(IntegrationNotConfigured):
        KaspiSmartPosPort().start("key", 100)
    assert DenyAllFiscalPolicy().evaluate().allowed is False


def test_production_module_does_not_name_fakes() -> None:
    source = (ROOT / "station-app/agzs_station/composition_production.py").read_text(encoding="utf-8")
    assert "payments.fake" not in source
    assert "FakePaymentPort" not in source
    assert "adapters.emulator" not in source


def test_production_process_does_not_import_fakes(tmp_path: Path) -> None:
    script = f"""
import sys
from pathlib import Path
from agzs_station.composition import build
from agzs_station.config import Settings
build(Settings(
    env="production",
    pump_driver="aska02",
    data_dir=Path({str(tmp_path)!r}),
    log_dir=Path({str(tmp_path)!r}),
    log_level="INFO",
    central_base_url="",
    database_url="",
    keyring_service_name="agzs-control",
))
assert "agzs_station.payments.fake" not in sys.modules
assert "agzs_station.pumps.adapters.emulator" not in sys.modules
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [
            str(ROOT / "shared"),
            str(ROOT / "station-app"),
            str(ROOT / "central-server"),
            env.get("PYTHONPATH", ""),
        ]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr


def test_server_package_does_not_pull_the_station() -> None:
    script = (
        "import sys, agzs_server; "
        "assert 'agzs_station' not in sys.modules; "
        "assert 'fastapi' not in sys.modules"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT / "shared"), str(ROOT / "central-server"), env.get("PYTHONPATH", "")]
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert completed.returncode == 0, completed.stderr
