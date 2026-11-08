import pytest

from agzs_shared.money import cost_kzt_test_half_up, format_tenge, max_ml_for_prepaid, parse_tenge


def test_parse_whole_tenge() -> None:
    assert parse_tenge(95) == 95
    assert format_tenge(95) == "95 тг"


def test_reject_fraction_and_bool() -> None:
    with pytest.raises(TypeError):
        parse_tenge(True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        parse_tenge(95.5)  # type: ignore[arg-type]


def test_reject_negative() -> None:
    with pytest.raises(ValueError):
        parse_tenge(-1)


def test_half_up_dose_does_not_exceed_prepaid_tenge() -> None:
    volume = max_ml_for_prepaid(95, 95)
    assert cost_kzt_test_half_up(volume, 95) == 95
    assert cost_kzt_test_half_up(volume + 1, 95) == 96
