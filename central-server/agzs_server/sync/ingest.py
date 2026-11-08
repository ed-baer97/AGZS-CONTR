"""Приём пачек. Повтор с тем же телом — DUPLICATE, с другим — REJECTED."""

import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from agzs_shared.integrity import canonical_json
from agzs_shared.ids import new_id
from agzs_server.db import AuditRecord, PumpRecord, SaleRecord, ShiftRecord, Station, SyncInbox
from agzs_server.security import token_hash

_SALE_FIELDS = (
    "shift_id",
    "pump_id",
    "display_number",
    "order_status",
    "payment_status",
    "fiscal_status",
    "dispensing_status",
    "settlement_status",
    "prepaid_kzt",
    "unit_price_kzt_per_liter",
    "delivered_ml",
    "actual_kzt",
    "delta_kzt",
)


def body_hash(payload: dict[str, object]) -> str:
    import hashlib

    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def accept_batch(session: Session, station: Station, events: list[dict[str, object]]) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    for event in events:
        event_id = str(event["event_id"])
        payload = event.get("payload")
        if not isinstance(payload, dict):
            results.append({"event_id": event_id, "status": "REJECTED"})
            continue
        digest = body_hash(payload)
        if payload.get("organization_id") not in {None, station.organization_id}:
            _audit(session, station, "sync.hash", "rejected-org")
            results.append({"event_id": event_id, "status": "REJECTED"})
            continue
        if payload.get("station_id") not in {None, station.id}:
            _audit(session, station, "sync.hash", "rejected-station")
            results.append({"event_id": event_id, "status": "REJECTED"})
            continue
        existing = session.get(SyncInbox, (station.id, event_id))
        if existing is not None:
            if existing.body_hash == digest:
                results.append({"event_id": event_id, "status": "DUPLICATE"})
            else:
                _audit(session, station, "sync.hash", "mismatch")
                results.append({"event_id": event_id, "status": "REJECTED"})
            continue
        session.add(
            SyncInbox(
                station_id=station.id,
                event_id=event_id,
                organization_id=station.organization_id,
                body_hash=digest,
                event_type=str(event["event_type"]),
                station_sequence=int(event["station_sequence"]),
                received_at=_now(),
            )
        )
        _project(session, station, str(event["event_type"]), payload)
        results.append({"event_id": event_id, "status": "ACCEPTED"})
    return results


def record_heartbeat(session: Session, station: Station, payload: dict[str, object]) -> None:
    from agzs_server.db import Heartbeat

    session.add(
        Heartbeat(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.id,
            received_at=_now(),
            payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )
    )
    pumps = payload.get("pumps")
    if not isinstance(pumps, list):
        return
    for item in pumps:
        if not isinstance(item, dict) or "pump_id" not in item:
            continue
        pump = session.get(PumpRecord, str(item["pump_id"]))
        if pump is None:
            session.add(
                PumpRecord(
                    id=str(item["pump_id"]),
                    organization_id=station.organization_id,
                    station_id=station.id,
                    name=str(item.get("name") or "Колонка"),
                    price_kzt_per_liter=int(item.get("price_kzt_per_liter") or 0),
                    status=str(item.get("status") or "OFFLINE"),
                    observation_quality=str(item.get("observation_quality") or "UNKNOWN"),
                )
            )
            continue
        if "status" in item:
            pump.status = str(item["status"])
        if "observation_quality" in item:
            pump.observation_quality = str(item["observation_quality"])
        if "name" in item:
            pump.name = str(item["name"])


def config_document(session: Session, station: Station) -> dict[str, object]:
    pumps = session.scalars(select(PumpRecord).where(PumpRecord.station_id == station.id)).all()
    return {
        "revision": station.config_revision,
        "sales_blocked": station.sales_blocked,
        "rounding_policy_id": station.rounding_policy_id,
        "fiscal_policy_id": station.fiscal_policy_id,
        "pumps": [
            {
                "pump_id": pump.id,
                "name": pump.name,
                "price_kzt_per_liter": pump.price_kzt_per_liter,
            }
            for pump in pumps
        ],
    }


def device_for_token(session: Session, token: str) -> tuple[object, Station] | None:
    from agzs_server.db import Device

    device = session.scalar(select(Device).where(Device.token_hash == token_hash(token), Device.revoked.is_(False)))
    if device is None:
        return None
    station = session.get(Station, device.station_id)
    if station is None:
        return None
    return device, station


def _project(session: Session, station: Station, event_type: str, payload: dict[str, object]) -> None:
    if event_type == "shift.opened":
        shift_id = str(payload.get("shift_id") or "")
        if shift_id and session.get(ShiftRecord, shift_id) is None:
            session.add(
                ShiftRecord(
                    id=shift_id,
                    organization_id=station.organization_id,
                    station_id=station.id,
                    number=int(payload.get("number") or 0),
                    status="OPEN",
                    user_id=None if payload.get("user_id") is None else str(payload["user_id"]),
                )
            )
        return
    if event_type == "shift.closed":
        shift = session.get(ShiftRecord, str(payload.get("shift_id") or ""))
        if shift is not None:
            shift.status = "CLOSED"
        return
    if event_type == "pump.fault":
        _audit(session, station, "pump.fault", str(payload.get("code") or "fault"))
        return
    sale_id = payload.get("sale_id")
    if not isinstance(sale_id, str):
        return
    sale = session.get(SaleRecord, sale_id)
    if sale is None:
        if "display_number" not in payload:
            return
        sale = SaleRecord(
            id=sale_id,
            organization_id=station.organization_id,
            station_id=station.id,
            display_number=str(payload["display_number"]),
            prepaid_kzt=int(payload.get("prepaid_kzt") or 0),
            unit_price_kzt_per_liter=int(payload.get("unit_price_kzt_per_liter") or 0),
            created_at=_now(),
        )
        session.add(sale)
    for field in _SALE_FIELDS:
        if field in payload and payload[field] is not None:
            setattr(sale, field, payload[field])


def _audit(session: Session, station: Station, action: str, result: str) -> None:
    session.add(
        AuditRecord(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.id,
            occurred_at=_now(),
            action=action,
            result=result,
            details_json="{}",
        )
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
