import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from agzs_server.api.app import create_app
from agzs_server.db import Heartbeat, SaleRecord
from agzs_server.sync.ingest import record_heartbeat
from agzs_station.database.models import StationProfile
from agzs_station.sync.deliver import publish_outbox
from tests.integration.test_virtual_sale import _ready


def _office(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("CENTRAL_BOOTSTRAP_USER", "platform")
    monkeypatch.setenv("CENTRAL_BOOTSTRAP_PASSWORD", "platform-pass")
    app = create_app(f"sqlite:///{tmp_path / 'central.db'}")
    from fastapi.testclient import TestClient

    client = TestClient(app)
    login = client.post("/api/v1/auth/login", json={"username": "platform", "password": "platform-pass"})
    assert login.status_code == 200
    return client, login.json()["token"], app


def test_batch_is_idempotent_and_hidden_from_another_organization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _adapter, clock, factory, pumps = _ready(tmp_path)
    sale = service.begin_prepay(pumps[0].pump_id, 10)
    service.take_payment(sale.sale_id)
    service.open_dispense_gate(sale.sale_id)
    clock.advance_ms(200)
    service.tick()
    with factory() as session:
        profile = session.scalar(select(StationProfile).limit(1))
        assert profile is not None
        organization_id = profile.organization_id
        station_id = profile.station_id

    client, token, _app = _office(monkeypatch, tmp_path)
    platform = {"Authorization": f"Bearer {token}", "X-Organization-Id": organization_id}
    assert client.post("/api/v1/organizations", json={"id": organization_id, "name": "Сеть А"}, headers=platform).status_code == 200
    other = client.post("/api/v1/organizations", json={"name": "Сеть Б"}, headers=platform).json()
    assert client.post(
        "/api/v1/stations",
        json={"id": station_id, "code": "VIRT", "name": "Учебная АГЗС", "timezone": "Asia/Almaty"},
        headers=platform,
    ).status_code == 200
    user_a = {"Authorization": f"Bearer {token}", "X-Organization-Id": organization_id}
    assert client.post(
        "/api/v1/users",
        json={"username": "org-a", "password": "org-a-pass", "role_code": "org_admin"},
        headers=user_a,
    ).status_code == 200
    user_b_headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": other["id"]}
    assert client.post(
        "/api/v1/users",
        json={"username": "org-b", "password": "org-b-pass", "role_code": "auditor"},
        headers=user_b_headers,
    ).status_code == 200
    code = client.post("/api/v1/pairing-codes", json={"station_id": station_id}, headers=platform)
    device = client.post("/api/v1/devices/pair", json={"code": code.json()["code"]})
    assert device.status_code == 200
    device_headers = {"X-Device-Token": device.json()["token"]}

    def post(events: list[dict[str, object]]) -> list[dict[str, object]]:
        response = client.post("/api/v1/sync/batches", json={"events": events}, headers=device_headers)
        assert response.status_code == 200
        return response.json()["results"]

    events = service.pending_outbox()
    first = post(events)
    assert any(item["status"] == "ACCEPTED" for item in first)
    second = post(events)
    assert second
    assert all(item["status"] == "DUPLICATE" for item in second)
    changed = json.loads(json.dumps(events))
    sale_event = next(item for item in changed if item["event_type"] == "sale.created")
    sale_event["payload"]["prepaid_kzt"] = 1
    mismatch = post([sale_event])
    assert mismatch[0]["status"] == "REJECTED"

    login_a = client.post("/api/v1/auth/login", json={"username": "org-a", "password": "org-a-pass"})
    login_b = client.post("/api/v1/auth/login", json={"username": "org-b", "password": "org-b-pass"})
    seen = client.get(
        "/api/v1/sales",
        headers={"Authorization": f"Bearer {login_a.json()['token']}", "X-Organization-Id": organization_id},
    )
    assert seen.status_code == 200
    assert seen.json()[0]["prepaid_kzt"] == 10
    hidden = client.get(
        "/api/v1/sales",
        headers={"Authorization": f"Bearer {login_b.json()['token']}", "X-Organization-Id": other["id"]},
    )
    assert hidden.json() == []
    forbidden = client.get(
        "/api/v1/sales",
        headers={"Authorization": f"Bearer {login_b.json()['token']}", "X-Organization-Id": organization_id},
    )
    assert forbidden.status_code == 403
    assert forbidden.headers["content-type"].startswith("application/problem+json")

    blocked = client.put(
        f"/api/v1/stations/{station_id}/sales-block",
        json={"blocked": True},
        headers=platform,
    )
    assert blocked.status_code == 200
    beat = client.post(
        "/api/v1/sync/heartbeat",
        json={"driver": "emulator", "outbox_depth": 0, "pumps": []},
        headers=device_headers,
    )
    assert beat.status_code == 200
    config = client.get("/api/v1/sync/config", headers=device_headers).json()
    assert config["sales_blocked"] is True
    assert "authorize" not in config
    assert "stop" not in config
    assert "dispense" not in config
    publish_outbox(service, post)


def test_routes_have_no_pump_commands(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _client, _token, app = _office(monkeypatch, tmp_path)
    banned = ("dispense", "authorize", "start-pump", "stop-pump", "dose", "nozzle", "valve")
    paths = [getattr(route, "path", "") for route in app.routes]
    assert paths
    for path in paths:
        lowered = path.lower()
        for token in banned:
            assert token not in lowered
    source = Path("central-server/agzs_server/db.py").read_text(encoding="utf-8")
    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source


def test_login_is_rate_limited(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _token, _app = _office(monkeypatch, tmp_path)
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json={"username": "platform", "password": "wrong"}).status_code == 401
    blocked = client.post("/api/v1/auth/login", json={"username": "platform", "password": "platform-pass"})
    assert blocked.status_code == 403


def test_many_heartbeats_do_not_create_sales(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _client, token, app = _office(monkeypatch, tmp_path)
    headers = {"Authorization": f"Bearer {token}", "X-Organization-Id": "org"}
    from fastapi.testclient import TestClient

    client = TestClient(app)
    client.post("/api/v1/organizations", json={"id": "org", "name": "Сеть"}, headers=headers)
    client.post(
        "/api/v1/stations",
        json={"id": "station", "code": "S", "name": "Станция", "timezone": "Asia/Almaty"},
        headers=headers,
    )
    with app.state.factory() as session:
        from agzs_server.db import Station

        station = session.get(Station, "station")
        for index in range(1000):
            record_heartbeat(session, station, {"driver": "emulator", "outbox_depth": index, "pumps": []})
        session.commit()
        assert session.scalar(select(func.count()).select_from(Heartbeat)) == 1000
        assert session.scalar(select(func.count()).select_from(SaleRecord)) == 0


def test_monitor_page_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _token, _app = _office(monkeypatch, tmp_path)
    page = client.get("/monitor")
    assert page.status_code == 200
    assert "Команд колонке отсюда нет" in page.text
    health = client.get("/health")
    assert health.json()["status"] == "ok"
