"""Целые тенге.

Одна единица равна 1 KZT. Дробный тиын не представляется:
это решение заказчика перед этапом 2. Отрицательные суммы
сюда не входят; знаковое расхождение продажи хранится отдельно.
"""


def parse_tenge(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Сумма задаётся целым числом тенге")
    if value < 0:
        raise ValueError("Сумма в тенге не может быть отрицательной")
    return value


def format_tenge(value: int) -> str:
    amount = parse_tenge(value)
    return f"{amount} тг"


def test_half_up_div(numerator: int, denominator: int) -> int:
    """Половинное округление вверх для неотрицательных чисел.

    Это испытательная политика TEST_HALF_UP, а не нормативное правило кассы.
    """

    if isinstance(numerator, bool) or isinstance(denominator, bool):
        raise TypeError("Округление считается по целым числам")
    if not isinstance(numerator, int) or not isinstance(denominator, int):
        raise TypeError("Округление считается по целым числам")
    if numerator < 0 or denominator <= 0:
        raise ValueError("Округление принимает неотрицательное числимое и положительный знаменатель")
    return (numerator + denominator // 2) // denominator


def cost_kzt_test_half_up(delivered_ml: int, price_kzt_per_liter: int) -> int:
    parse_tenge(price_kzt_per_liter)
    if isinstance(delivered_ml, bool) or not isinstance(delivered_ml, int) or delivered_ml < 0:
        raise ValueError("Объём задаётся неотрицательным целым числом миллилитров")
    return test_half_up_div(delivered_ml * price_kzt_per_liter, 1000)


def max_ml_for_prepaid(amount_kzt: int, price_kzt_per_liter: int) -> int:
    """Сколько миллилитров можно налить, чтобы TEST_HALF_UP не превысил предоплату."""

    amount = parse_tenge(amount_kzt)
    price = parse_tenge(price_kzt_per_liter)
    if price == 0 or amount == 0:
        return 0
    return (amount * 1000 + 499) // price
