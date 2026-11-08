import pytest

from agzs_station.ui.indicators import (
    face_digits,
    format_money,
    format_volume,
    lamp_color,
    preset_kzt,
    status_caption,
)


def test_link_loss_hides_digits_and_a_finished_dose_stays_on_the_face() -> None:
    assert status_caption("DISPENSING", "UNKNOWN") == "НЕТ СВЯЗИ"
    assert status_caption("OFFLINE", "CONFIRMED") == "ОТКЛЮЧЕНА"
    assert status_caption("DISPENSING", "CONFIRMED") == "НАЛИВ"
    assert lamp_color("НАЛИВ") == "#1ecb4a"
    assert format_money(1500, hidden=False) == "001500"
    assert format_volume(200, hidden=False) == "0000.20"
    assert format_volume(None, hidden=True) == "----.--"

    money, volume, remembered_ml, remembered_kzt = face_digits(
        status="DISPENSING",
        observation_quality="CONFIRMED",
        dose_ml=1250,
        provisional_kzt=125,
        remembered_ml=None,
        remembered_kzt=None,
    )
    assert (money, volume, remembered_ml, remembered_kzt) == ("000125", "0001.25", 1250, 125)
    frozen = face_digits(
        status="IDLE",
        observation_quality="CONFIRMED",
        dose_ml=None,
        provisional_kzt=None,
        remembered_ml=remembered_ml,
        remembered_kzt=remembered_kzt,
    )
    assert frozen[:2] == ("000125", "0001.25")
    hidden = face_digits(
        status="OFFLINE",
        observation_quality="UNKNOWN",
        dose_ml=None,
        provisional_kzt=None,
        remembered_ml=remembered_ml,
        remembered_kzt=remembered_kzt,
    )
    assert hidden[:2] == ("------", "----.--")
    assert hidden[2:] == (1250, 125)


def test_preset_accepts_tenge_and_liters() -> None:
    assert preset_kzt("1500", "money", 100) == 1500
    assert preset_kzt("0.20", "volume", 100) == 20
    assert preset_kzt("1,5", "volume", 100) == 150
    with pytest.raises(ValueError):
        preset_kzt("1.234", "volume", 100)
    with pytest.raises(ValueError):
        preset_kzt("0", "money", 100)
