"""Учебное правило без чека. В промышленную сборку этот модуль не входит.

Правило не описывает документы Webkassa. Оно только открывает учебный отпуск
на эмуляторе и прямо говорит, что расчёт с покупателем не проводится.
"""

from agzs_station.fiscal.policy import GateDecision


class TrainingNoReceiptPolicy:
    policy_id = "TRAINING_NO_RECEIPT"

    def evaluate(self, sale: object | None = None) -> GateDecision:
        del sale
        return GateDecision(
            allowed=True,
            reason="Учебный отпуск без чека. Это не касса и не расчёт с покупателем",
        )

    def steps_for(self, sale: object | None = None, moment: str = "") -> tuple[str, ...]:
        del sale, moment
        return ()
