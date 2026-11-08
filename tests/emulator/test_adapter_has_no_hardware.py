from pathlib import Path

import pytest

from agzs_station.errors import EmulatorNotReady
from agzs_station.pumps.adapters.emulator import EmulatorAdapter

ROOT = Path(__file__).resolve().parents[2]
_BANNED = ("serial", "/dev", "pyserial", "socket", "baud", "0x", "искра", "iskra")


def test_emulator_has_no_hardware_channel() -> None:
    source = (ROOT / "station-app/agzs_station/pumps/adapters/emulator.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for token in _BANNED:
        assert token not in lowered
    adapter = EmulatorAdapter()
    assert not hasattr(adapter, "port")
    with pytest.raises(EmulatorNotReady):
        adapter.probe()


def test_aska_placeholder_has_no_protocol_bytes() -> None:
    source = (ROOT / "station-app/agzs_station/pumps/adapters/aska02.py").read_text(encoding="utf-8")
    lowered = source.lower()
    for token in _BANNED:
        assert token not in lowered
