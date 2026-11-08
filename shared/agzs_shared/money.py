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
