"""Доставка outbox. Повтор не создаёт вторую продажу: это решает центр."""

from collections.abc import Callable

from agzs_station.application.cashier import CashierService


def publish_outbox(
    service: CashierService,
    post: Callable[[list[dict[str, object]]], list[dict[str, object]]],
) -> list[dict[str, object]]:
    events = service.pending_outbox()
    if not events:
        return []
    results = post(events)
    accepted = {
        str(item["event_id"])
        for item in results
        if item.get("status") in {"ACCEPTED", "DUPLICATE"}
    }
    if accepted:
        service.mark_published(accepted)
    return results
