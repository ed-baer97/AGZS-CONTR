"""Сравнение предоплаты и факта. Возврат сам не создаётся."""

from agzs_shared.enums import SettlementStatus
from agzs_shared.money import cost_kzt_test_half_up

TEST_HALF_UP = "TEST_HALF_UP"


def actual_kzt(delivered_ml: int, price_kzt_per_liter: int, policy_id: str) -> int:
    if policy_id != TEST_HALF_UP:
        raise ValueError("Нет утверждённого правила округления для этой цены")
    return cost_kzt_test_half_up(delivered_ml, price_kzt_per_liter)


def classify(prepaid_kzt: int, actual: int) -> tuple[str, int, bool]:
    """Возвращает статус расхождения, дельту (факт минус предоплата) и признак перелива."""

    delta = actual - prepaid_kzt
    if delta == 0:
        return SettlementStatus.BALANCED, delta, False
    if delta < 0:
        return SettlementStatus.ADJUSTMENT_REQUIRED, delta, False
    return SettlementStatus.NOT_READY, delta, True
