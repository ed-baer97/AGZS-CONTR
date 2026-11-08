from dataclasses import dataclass


@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


class DenyAllFiscalPolicy:
    """Единственное встроенное правило: чек не выдумывается, газ не отпускается."""

    policy_id = "DENY_ALL"

    def evaluate(self) -> GateDecision:
        return GateDecision(allowed=False, reason="Фискальная политика не утверждена")
