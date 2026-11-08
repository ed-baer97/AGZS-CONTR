"""Дописывание журналов и outbox. Бизнес-ветвлений продажи здесь нет."""

import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from agzs_shared.events import SCHEMA_VERSION
from agzs_shared.ids import new_id
from agzs_shared.integrity import GENESIS_HASH, canonical_json, chain_hash
from agzs_station.database.models import AuditLog, NumberSequence, OutboxEvent, PumpEvent, SaleEvent
from agzs_station.database.time import utc_now

_TABLES = {
    "pump_events": "payload_json",
    "sale_events": "reason",
    "audit_log": "details_json",
}


def next_number(session: Session, name: str) -> int:
    row = session.get(NumberSequence, name)
    if row is None:
        session.add(NumberSequence(name=name, value=1))
        session.flush()
        return 1
    row.value += 1
    session.flush()
    return row.value


def append_pump_event(
    session: Session,
    *,
    organization_id: str,
    station_id: str,
    pump_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str | None,
    sale_id: str | None = None,
    payload: dict[str, object] | None = None,
) -> PumpEvent:
    body = {
        "event_type": event_type,
        "from_status": from_status,
        "pump_id": pump_id,
        "sale_id": sale_id,
        "to_status": to_status,
    }
    if payload:
        body.update(payload)
    prev, digest, encoded = _chain(session, "pump_events", body)
    row = PumpEvent(
        id=new_id(),
        organization_id=organization_id,
        station_id=station_id,
        pump_id=pump_id,
        sale_id=sale_id,
        event_type=event_type,
        from_status=from_status,
        to_status=to_status,
        payload_json=encoded,
        occurred_at=utc_now(),
        prev_hash=prev,
        row_hash=digest,
    )
    session.add(row)
    session.flush()
    return row


def append_sale_event(
    session: Session,
    *,
    sale_id: str,
    axis: str,
    from_value: str | None,
    to_value: str,
    reason: str,
    payment_attempt_id: str | None = None,
    fiscal_document_id: str | None = None,
    authorization_id: str | None = None,
) -> SaleEvent:
    body = {
        "axis": axis,
        "authorization_id": authorization_id,
        "fiscal_document_id": fiscal_document_id,
        "from_value": from_value,
        "payment_attempt_id": payment_attempt_id,
        "reason": reason,
        "sale_id": sale_id,
        "to_value": to_value,
    }
    prev, digest, _encoded = _chain(session, "sale_events", body)
    row = SaleEvent(
        id=new_id(),
        sale_id=sale_id,
        occurred_at=utc_now(),
        axis=axis,
        from_value=from_value,
        to_value=to_value,
        reason=reason,
        payment_attempt_id=payment_attempt_id,
        fiscal_document_id=fiscal_document_id,
        authorization_id=authorization_id,
        prev_hash=prev,
        row_hash=digest,
    )
    session.add(row)
    session.flush()
    return row


def append_audit(
    session: Session,
    *,
    organization_id: str,
    station_id: str,
    actor_user_id: str | None,
    action: str,
    object_type: str,
    object_id: str | None,
    result: str,
    details: dict[str, object] | None = None,
) -> AuditLog:
    body = {
        "action": action,
        "actor_user_id": actor_user_id,
        "object_id": object_id,
        "object_type": object_type,
        "result": result,
        "station_id": station_id,
    }
    if details:
        body["details"] = details
    prev, digest, encoded = _chain(session, "audit_log", body)
    row = AuditLog(
        id=new_id(),
        organization_id=organization_id,
        station_id=station_id,
        occurred_at=utc_now(),
        actor_user_id=actor_user_id,
        action=action,
        object_type=object_type,
        object_id=object_id,
        result=result,
        details_json=encoded,
        prev_hash=prev,
        row_hash=digest,
    )
    session.add(row)
    session.flush()
    return row


def enqueue(
    session: Session,
    *,
    organization_id: str,
    station_id: str,
    event_type: str,
    payload: dict[str, object],
) -> OutboxEvent:
    sequence = next_number(session, "outbox")
    row = OutboxEvent(
        event_id=new_id(),
        organization_id=organization_id,
        station_id=station_id,
        station_sequence=sequence,
        event_type=event_type,
        schema_version=SCHEMA_VERSION,
        payload_json=canonical_json(payload),
        created_at=utc_now(),
    )
    session.add(row)
    session.flush()
    return row


def verify_chain(session: Session, table: str) -> bool:
    column = _TABLES.get(table)
    if column is None:
        raise ValueError("Неизвестная таблица журнала")
    if table == "sale_events":
        rows = session.execute(
            text(
                "SELECT prev_hash, row_hash, sale_id, axis, from_value, to_value, reason, "
                "payment_attempt_id, fiscal_document_id, authorization_id "
                "FROM sale_events ORDER BY rowid"
            )
        ).all()
        previous = GENESIS_HASH
        for row in rows:
            payload = {
                "axis": row.axis,
                "authorization_id": row.authorization_id,
                "fiscal_document_id": row.fiscal_document_id,
                "from_value": row.from_value,
                "payment_attempt_id": row.payment_attempt_id,
                "reason": row.reason,
                "sale_id": row.sale_id,
                "to_value": row.to_value,
            }
            if row.prev_hash != previous or chain_hash(previous, payload) != row.row_hash:
                return False
            previous = row.row_hash
        return True
    rows = session.execute(
        text(f"SELECT prev_hash, row_hash, {column} FROM {table} ORDER BY rowid")
    ).all()
    previous = GENESIS_HASH
    for prev_hash, row_hash, payload_json in rows:
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            return False
        if prev_hash != previous or chain_hash(previous, payload) != row_hash:
            return False
        previous = row_hash
    return True


def _chain(session: Session, table: str, payload: dict[str, object]) -> tuple[str, str, str]:
    if table not in _TABLES:
        raise ValueError("Неизвестная таблица журнала")
    previous = session.execute(
        text(f"SELECT row_hash FROM {table} ORDER BY rowid DESC LIMIT 1")
    ).scalar_one_or_none()
    prev = previous or GENESIS_HASH
    encoded = canonical_json(payload)
    return prev, chain_hash(prev, payload), encoded
