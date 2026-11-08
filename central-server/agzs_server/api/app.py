"""HTTP API центра. Маршрутов пуска и останова газа нет."""

import json
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from agzs_shared.ids import new_id
from agzs_server.db import (
    AuditRecord,
    ConfigChange,
    Device,
    Heartbeat,
    Membership,
    OfficeSession,
    OfficeUser,
    Organization,
    PairingCode,
    PriceVersion,
    PumpRecord,
    SaleRecord,
    Station,
    bind_tenant,
    default_database_url,
    make_engine,
    prepare,
    session_factory,
)
from agzs_server.security import LoginLimiter, hash_password, new_token, token_hash, verify_password
from agzs_server.sync.ingest import accept_batch, config_document, device_for_token, record_heartbeat

_MONITOR = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8"><title>AGZS мониторинг</title></head>
<body>
<h1>Мониторинг сети</h1>
<p>Пульт читает уже принятые пульсы. Команд колонке отсюда нет.</p>
<form id="login">
<label>Имя <input name="username"></label>
<label>Пароль <input name="password" type="password"></label>
<label>Организация <input name="organization"></label>
<button type="submit">Войти</button>
</form>
<pre id="out"></pre>
<script>
document.getElementById("login").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = new FormData(event.target);
  const login = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({username: form.get("username"), password: form.get("password")})
  });
  const session = await login.json();
  const data = await fetch("/api/v1/monitoring/stations", {
    headers: {"Authorization": "Bearer " + session.token, "X-Organization-Id": form.get("organization")}
  });
  document.getElementById("out").textContent = JSON.stringify(await data.json(), null, 2);
});
</script>
</body></html>
"""


def create_app(database_url: str | None = None) -> FastAPI:
    engine = make_engine(database_url or default_database_url())
    prepare(engine)
    factory = session_factory(engine)
    limiter = LoginLimiter()
    _bootstrap_admin(factory)
    app = FastAPI(title="AGZS Control", version="0.3.0")
    app.state.factory = factory
    app.state.limiter = limiter

    @app.exception_handler(PermissionError)
    def _denied(_request: Request, exc: PermissionError) -> JSONResponse:
        status = 401 if "вход" in str(exc) else 403
        return _problem(status, str(exc))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/monitor", response_class=HTMLResponse)
    def monitor() -> str:
        return _MONITOR

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> dict[str, object]:
        body = await request.json()
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        address = request.client.host if request.client else ""
        if limiter.blocked(username, address):
            raise PermissionError("Вход временно заблокирован")
        with factory() as session:
            user = session.scalar(select(OfficeUser).where(OfficeUser.username == username))
            if user is None or not user.is_active or not verify_password(user.password_hash, password):
                limiter.fail(username, address)
                _office_audit(session, None, "login", "denied")
                session.commit()
                raise PermissionError("Нужен вход")
            limiter.reset(username, address)
            token = new_token()
            session.add(
                OfficeSession(
                    token_hash=token_hash(token),
                    user_id=user.id,
                    expires_at=(datetime.now(UTC) + timedelta(hours=12)).isoformat(),
                )
            )
            _office_audit(session, user, "login", "ok")
            session.commit()
            return {"token": token, "platform_admin": user.platform_admin}

    @app.post("/api/v1/auth/logout")
    def logout(request: Request) -> dict[str, str]:
        user, session, raw = _office(request, factory)
        del user
        row = session.scalar(select(OfficeSession).where(OfficeSession.token_hash == token_hash(raw)))
        if row is not None:
            session.delete(row)
        session.commit()
        return {"status": "ok"}

    @app.post("/api/v1/organizations")
    async def create_organization(request: Request) -> dict[str, str]:
        user, session, _raw = _office(request, factory)
        if not user.platform_admin:
            raise PermissionError("Недостаточно прав")
        body = await request.json()
        org = Organization(id=str(body.get("id") or new_id()), name=str(body["name"]))
        session.add(org)
        session.commit()
        return {"id": org.id, "name": org.name}

    @app.get("/api/v1/organizations")
    def list_organizations(request: Request) -> list[dict[str, str]]:
        user, session, _raw = _office(request, factory)
        if user.platform_admin:
            rows = session.scalars(select(Organization)).all()
        else:
            ids = session.scalars(select(Membership.organization_id).where(Membership.user_id == user.id)).all()
            rows = session.scalars(select(Organization).where(Organization.id.in_(ids))).all()
        return [{"id": row.id, "name": row.name} for row in rows]

    @app.post("/api/v1/users")
    async def create_user(request: Request) -> dict[str, str]:
        actor, session, _raw = _office(request, factory)
        body = await request.json()
        org_id = _require_org(request, session, actor)
        if not actor.platform_admin:
            member = session.get(Membership, (actor.id, org_id))
            if member is None or member.role_code not in {"org_admin", "station_manager"}:
                session.close()
                raise PermissionError("Недостаточно прав")
        created = OfficeUser(
            id=new_id(),
            username=str(body["username"]),
            password_hash=hash_password(str(body["password"])),
        )
        session.add(created)
        session.add(
            Membership(
                user_id=created.id,
                organization_id=org_id,
                role_code=str(body.get("role_code") or "auditor"),
            )
        )
        session.commit()
        session.close()
        return {"id": created.id}

    @app.post("/api/v1/stations")
    async def create_station(request: Request) -> dict[str, str]:
        user, session, _raw = _office(request, factory)
        body = await request.json()
        org_id = _require_org(request, session, user)
        station = Station(
            id=str(body.get("id") or new_id()),
            organization_id=org_id,
            code=str(body["code"]),
            name=str(body["name"]),
            timezone=str(body.get("timezone") or "Asia/Almaty"),
        )
        session.add(station)
        session.commit()
        return {"id": station.id}

    @app.post("/api/v1/pairing-codes")
    async def pairing_code(request: Request) -> dict[str, str]:
        user, session, _raw = _office(request, factory)
        body = await request.json()
        org_id = _require_org(request, session, user)
        station = session.get(Station, str(body["station_id"]))
        if station is None or station.organization_id != org_id:
            raise PermissionError("Станция не найдена")
        code = new_token()
        session.add(
            PairingCode(
                code=code,
                station_id=station.id,
                expires_at=(datetime.now(UTC) + timedelta(minutes=15)).isoformat(),
            )
        )
        session.commit()
        return {"code": code}

    @app.post("/api/v1/devices/pair")
    async def pair(request: Request) -> dict[str, str]:
        body = await request.json()
        with factory() as session:
            row = session.get(PairingCode, str(body.get("code") or ""))
            if row is None or row.used_at is not None or row.expires_at < datetime.now(UTC).isoformat():
                raise PermissionError("Код сопряжения недействителен")
            token = new_token()
            session.add(Device(id=new_id(), station_id=row.station_id, token_hash=token_hash(token)))
            row.used_at = datetime.now(UTC).isoformat()
            session.commit()
            return {"token": token}

    @app.post("/api/v1/sync/batches")
    async def batches(request: Request) -> dict[str, object]:
        body = await request.json()
        with factory() as session:
            found = device_for_token(session, _device_token(request))
            if found is None:
                raise PermissionError("Нужен вход")
            _device, station = found
            bind_tenant(session, station.organization_id, platform=False)
            results = accept_batch(session, station, list(body.get("events") or []))
            session.commit()
            return {"results": results}

    @app.post("/api/v1/sync/heartbeat")
    async def heartbeat(request: Request) -> dict[str, str]:
        body = await request.json()
        with factory() as session:
            found = device_for_token(session, _device_token(request))
            if found is None:
                raise PermissionError("Нужен вход")
            _device, station = found
            bind_tenant(session, station.organization_id, platform=False)
            record_heartbeat(session, station, body)
            session.commit()
            return {"status": "ok"}

    @app.get("/api/v1/sync/config")
    def sync_config(request: Request) -> dict[str, object]:
        with factory() as session:
            found = device_for_token(session, _device_token(request))
            if found is None:
                raise PermissionError("Нужен вход")
            _device, station = found
            return config_document(session, station)

    @app.put("/api/v1/stations/{station_id}/pumps/{pump_id}/price")
    async def set_price(station_id: str, pump_id: str, request: Request) -> dict[str, int]:
        user, session, _raw = _office(request, factory)
        org_id = _require_org(request, session, user)
        body = await request.json()
        station = session.get(Station, station_id)
        if station is None or station.organization_id != org_id:
            raise PermissionError("Станция не найдена")
        price = int(body["price_kzt_per_liter"])
        if price < 0:
            raise PermissionError("Цена не может быть отрицательной")
        pump = session.get(PumpRecord, pump_id)
        if pump is None or pump.station_id != station.id:
            raise PermissionError("Колонка не найдена")
        station.config_revision += 1
        pump.price_kzt_per_liter = price
        session.add(
            PriceVersion(
                id=new_id(),
                organization_id=org_id,
                station_id=station.id,
                pump_id=pump.id,
                price_kzt_per_liter=price,
                revision=station.config_revision,
                created_at=datetime.now(UTC).isoformat(),
            )
        )
        _store_config(session, station)
        session.commit()
        return {"revision": station.config_revision, "price_kzt_per_liter": price}

    @app.put("/api/v1/stations/{station_id}/sales-block")
    async def sales_block(station_id: str, request: Request) -> dict[str, object]:
        user, session, _raw = _office(request, factory)
        org_id = _require_org(request, session, user)
        body = await request.json()
        station = session.get(Station, station_id)
        if station is None or station.organization_id != org_id:
            raise PermissionError("Станция не найдена")
        station.sales_blocked = bool(body["blocked"])
        station.config_revision += 1
        _store_config(session, station)
        session.commit()
        return {"sales_blocked": station.sales_blocked, "revision": station.config_revision}

    @app.get("/api/v1/sales")
    def sales(request: Request) -> list[dict[str, object]]:
        user, session, _raw = _office(request, factory)
        org_id = _require_org(request, session, user)
        rows = session.scalars(
            select(SaleRecord).where(SaleRecord.organization_id == org_id).order_by(SaleRecord.created_at)
        ).all()
        return [
            {
                "sale_id": row.id,
                "station_id": row.station_id,
                "display_number": row.display_number,
                "prepaid_kzt": row.prepaid_kzt,
                "actual_kzt": row.actual_kzt,
                "order_status": row.order_status,
            }
            for row in rows
        ]

    @app.get("/api/v1/reports/sales")
    def report(request: Request) -> dict[str, object]:
        user, session, _raw = _office(request, factory)
        org_id = _require_org(request, session, user)
        station_id = request.query_params.get("station_id")
        query = select(
            func.count(SaleRecord.id),
            func.coalesce(func.sum(SaleRecord.prepaid_kzt), 0),
            func.coalesce(func.sum(SaleRecord.actual_kzt), 0),
        ).where(SaleRecord.organization_id == org_id)
        if station_id:
            query = query.where(SaleRecord.station_id == station_id)
        count, prepaid, actual = session.execute(query).one()
        return {"count": int(count), "prepaid_kzt": int(prepaid), "actual_kzt": int(actual or 0)}

    @app.get("/api/v1/monitoring/stations")
    def monitoring(request: Request) -> list[dict[str, object]]:
        user, session, _raw = _office(request, factory)
        org_id = _require_org(request, session, user)
        stations = session.scalars(select(Station).where(Station.organization_id == org_id)).all()
        result = []
        for station in stations:
            beat = session.scalar(
                select(Heartbeat)
                .where(Heartbeat.station_id == station.id)
                .order_by(Heartbeat.received_at.desc())
            )
            result.append(
                {
                    "station_id": station.id,
                    "name": station.name,
                    "sales_blocked": station.sales_blocked,
                    "heartbeat": None if beat is None else json.loads(beat.payload_json),
                }
            )
        return result

    return app


def _problem(status: int, detail: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={"type": "about:blank", "title": detail, "status": status, "detail": detail},
    )


def _office(request: Request, factory: sessionmaker[Session]) -> tuple[OfficeUser, Session, str]:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise PermissionError("Нужен вход")
    raw = header.split(" ", 1)[1].strip()
    session = factory()
    row = session.scalar(select(OfficeSession).where(OfficeSession.token_hash == token_hash(raw)))
    if row is None or row.expires_at < datetime.now(UTC).isoformat():
        session.close()
        raise PermissionError("Нужен вход")
    user = session.get(OfficeUser, row.user_id)
    if user is None or not user.is_active:
        session.close()
        raise PermissionError("Нужен вход")
    return user, session, raw


def _require_org(request: Request, session: Session, user: OfficeUser) -> str:
    org_id = request.headers.get("x-organization-id", "").strip()
    if not org_id:
        raise PermissionError("Нужен контекст организации")
    if user.platform_admin:
        bind_tenant(session, org_id, platform=True)
        return org_id
    member = session.get(Membership, (user.id, org_id))
    if member is None:
        raise PermissionError("Недостаточно прав")
    bind_tenant(session, org_id, platform=False)
    return org_id


def _device_token(request: Request) -> str:
    token = request.headers.get("x-device-token", "").strip()
    if not token:
        raise PermissionError("Нужен вход")
    return token


def _store_config(session: Session, station: Station) -> None:
    document = config_document(session, station)
    session.add(
        ConfigChange(
            id=new_id(),
            organization_id=station.organization_id,
            station_id=station.id,
            revision=station.config_revision,
            document_json=json.dumps(document, ensure_ascii=False),
            created_at=datetime.now(UTC).isoformat(),
        )
    )


def _office_audit(session: Session, user: OfficeUser | None, action: str, result: str) -> None:
    session.add(
        AuditRecord(
            id=new_id(),
            organization_id="" if user is None else "platform",
            occurred_at=datetime.now(UTC).isoformat(),
            action=action,
            result=result,
            details_json="{}",
        )
    )


def _bootstrap_admin(factory: sessionmaker[Session]) -> None:
    import os

    username = os.environ.get("CENTRAL_BOOTSTRAP_USER", "").strip()
    password = os.environ.get("CENTRAL_BOOTSTRAP_PASSWORD", "").strip()
    if not username or not password:
        return
    with factory() as session:
        if session.scalar(select(OfficeUser).limit(1)) is not None:
            return
        session.add(
            OfficeUser(
                id=new_id(),
                username=username,
                password_hash=hash_password(password),
                platform_admin=True,
            )
        )
        session.commit()
