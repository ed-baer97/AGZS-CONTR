"""Локальный журнал. Слушает только 127.0.0.1."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from agzs.capabilities import facts
from agzs.controller import Controller, OperatorError, SystemClock
from agzs.dispenser import Dispenser
from agzs.export import events_csv, events_text, sales_csv, sales_json
from agzs.format import (
    PAYMENTS,
    fmt_dt,
    grouped_liters,
    grouped_money,
    parse_liters,
    parse_money,
)
from agzs.link import InProcessLink, SerialLink
from agzs.protocol import STATUS
from agzs.seed import seed_demo
from agzs.store import Store

WEB = Path(__file__).resolve().parent / "web" / "index.html"
MAX_BODY = 8_192


class App:
    def __init__(self, controller: Controller, store: Store):
        self.controller = controller
        self.store = store
        self.lock = threading.RLock()


def open_app(path: str | Path, serial: str | None = None) -> App:
    store = Store(path)
    if not store.has_shifts():
        seed_demo(store)
    price = int(store.meta("price_tiyn") or "0")
    accounted = int(store.meta("accounted_ml") or "0")
    if serial:
        link = SerialLink(serial)
        simulator = None
        store.set_meta("channel", "serial")
    else:
        simulator = Dispenser(number=1, price_tiyn=price, totalizer_ml=accounted)
        link = InProcessLink(simulator)
        if store.meta("channel") is None:
            store.set_meta("channel", "simulator")
    controller = Controller(
        link,
        store,
        clock=SystemClock(),
        column_no=1,
        simulator=simulator,
    )
    controller.restore()
    return App(controller, store)


def serve(app: App, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(app))
    server.daemon_threads = True
    return server


def _handler(app: App) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            _route(self, app, "GET")

        def do_POST(self) -> None:
            _route(self, app, "POST")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def _route(handler: BaseHTTPRequestHandler, app: App, method: str) -> None:
    parsed = urlparse(handler.path)
    path = parsed.path
    query = parse_qs(parsed.query)
    try:
        if method == "GET" and path == "/":
            _bytes(handler, 200, WEB.read_bytes(), "text/html; charset=utf-8")
            return
        if method == "GET" and path == "/api/bootstrap":
            _json(handler, 200, _bootstrap(app))
            return
        if method == "GET" and path == "/api/capabilities":
            _json(handler, 200, {"facts": facts()})
            return
        if method == "GET" and path == "/api/shift":
            shift = _shift_from_query(app, query)
            _json(handler, 200, _shift_payload(app, shift))
            return
        if method == "GET" and path.startswith("/api/export/"):
            shift = _shift_from_query(app, query)
            _export(handler, app, path, shift)
            return
        if method == "POST":
            if handler.headers.get("X-AGZS") != "1":
                _json(handler, 403, {"error": "Запрос без заголовка X-AGZS"})
                return
            body = _body(handler)
            if body is None:
                return
            _post(handler, app, path, body)
            return
        _json(handler, 404, {"error": "Нет такого адреса"})
    except OperatorError as exc:
        _json(handler, 400, {"error": str(exc)})
    except ValueError as exc:
        _json(handler, 400, {"error": str(exc)})
    except KeyError as exc:
        _json(handler, 400, {"error": f"Не хватает поля {exc}"})


def _post(handler: BaseHTTPRequestHandler, app: App, path: str, body: dict[str, Any]) -> None:
    ctrl = app.controller
    with app.lock:
        if path == "/api/fill":
            _fill(ctrl, body)
        elif path == "/api/payment":
            ctrl.set_payment(int(body["sale_id"]), str(body["payment"]))
        elif path == "/api/panel":
            ctrl.panel_note(str(body["kind"]), str(body["text"]))
        elif path == "/api/shift/close":
            ctrl.close_shift(parse_liters(str(body["totalizer"])), parse_liters(str(body["shift_counter"])))
        elif path == "/api/shift/open":
            ctrl.open_shift(
                str(body.get("operator") or "оператор"),
                parse_money(str(body["price"])),
                parse_liters(str(body["totalizer"])),
            )
        else:
            _json(handler, 404, {"error": "Нет такого адреса"})
            return
        payload = _bootstrap(app)
    _json(handler, 200, payload)


def _fill(ctrl: Controller, body: dict[str, Any]) -> None:
    mode = str(body["mode"])
    payment = str(body.get("payment") or "unknown")
    value = str(body.get("value") or "")
    try:
        if mode == "liters":
            ctrl.perform_fill("литры", liters_ml=parse_liters(value), payment=payment)
        elif mode == "amount":
            ctrl.perform_fill("сумма", amount_tiyn_value=parse_money(value), payment=payment)
        elif mode == "full":
            ctrl.perform_fill("до полного", tank_ml=parse_liters(value), payment=payment)
        else:
            raise OperatorError("Режим: liters, amount или full")
    except OperatorError:
        if ctrl.phase != "idle":
            try:
                ctrl.reset()
            except OperatorError:
                pass
        raise


def _export(handler: BaseHTTPRequestHandler, app: App, path: str, shift: dict[str, Any]) -> None:
    number = shift["number"]
    with app.lock:
        if path == "/api/export/sales.csv":
            payload, kind, name = sales_csv(app.store, shift), "text/csv; charset=utf-8", f"prodazhi-smena-{number}.csv"
        elif path == "/api/export/events.csv":
            payload, kind, name = events_csv(app.store, shift), "text/csv; charset=utf-8", f"zhurnal-smena-{number}.csv"
        elif path == "/api/export/events.txt":
            payload = events_text(app.store, shift).encode("utf-8")
            kind, name = "text/plain; charset=utf-8", f"zhurnal-smena-{number}.txt"
        elif path == "/api/export/sales.json":
            payload, kind, name = sales_json(app.store, shift), "application/json; charset=utf-8", f"prodazhi-smena-{number}.json"
        else:
            _json(handler, 404, {"error": "Нет такой выгрузки"})
            return
    _bytes(handler, 200, payload, kind, name)


def _bootstrap(app: App) -> dict[str, Any]:
    ctrl = app.controller
    code = ctrl.sim.code if ctrl.sim is not None else ctrl.last_code
    shifts = []
    for shift in app.store.shifts():
        sales = app.store.sales(int(shift["id"]))
        shifts.append(
            {
                "number": shift["number"],
                "status": shift["status"],
                "opened_at": fmt_dt(shift["opened_at"]),
                "closed_at": fmt_dt(shift["closed_at"]) if shift["closed_at"] else "",
                "receipts": len(sales),
                "liters": grouped_liters(sum(int(item["dispensed_ml"]) for item in sales)),
                "amount": grouped_money(sum(int(item["amount_tiyn"]) for item in sales)),
            }
        )
    open_shift = app.store.open_shift_row()
    channel = app.store.meta("channel") or "simulator"
    return {
        "channel": channel,
        "banner": _banner(channel),
        "column_no": ctrl.column_no,
        "phase": ctrl.phase,
        "status_code": code,
        "status_text": STATUS.get(code, code),
        "price": grouped_money(ctrl.price_tiyn),
        "accounted": grouped_liters(ctrl.accounted_ml),
        "open_shift": None if open_shift is None else int(open_shift["number"]),
        "payments": [{"code": key, "label": label} for key, label in PAYMENTS.items()],
        "shifts": shifts,
    }


def _banner(channel: str) -> str:
    if channel == "serial":
        return (
            "Порт открыт. В журнал попадает то, что колонка ответила кадром Искра 1.72: "
            "цена, объём и код состояния. Суммарник на табло (9 0 2) в кадр не входит."
        )
    return (
        "Колонка не на линии. Смена 14 учебная: её собрал имитатор УЗГС-01. "
        "Живой пульт по протоколу Искра 1.72 отдаёт цену, остаток дозы и код состояния. "
        "Суммарник, температуру, ошибки 01–04 и вид оплаты он не передаёт. "
        "Суммарник в выгрузке учётный — программа сама складывает отпущенные литры."
    )


def _shift_payload(app: App, shift: dict[str, Any]) -> dict[str, Any]:
    events = app.store.events(int(shift["id"]))
    sales = app.store.sales(int(shift["id"]))
    by_payment: dict[str, dict[str, int]] = {}
    by_stop: dict[str, int] = {}
    for sale in sales:
        bucket = by_payment.setdefault(sale["payment"], {"count": 0, "ml": 0, "tiyn": 0})
        bucket["count"] += 1
        bucket["ml"] += int(sale["dispensed_ml"])
        bucket["tiyn"] += int(sale["amount_tiyn"])
        by_stop[sale["stop_reason"]] = by_stop.get(sale["stop_reason"], 0) + 1
    return {
        "number": shift["number"],
        "status": shift["status"],
        "operator": shift["operator"],
        "opened_at": fmt_dt(shift["opened_at"]),
        "closed_at": fmt_dt(shift["closed_at"]) if shift["closed_at"] else "",
        "summary": {
            "receipts": len(sales),
            "liters": grouped_liters(sum(int(item["dispensed_ml"]) for item in sales)),
            "amount": grouped_money(sum(int(item["amount_tiyn"]) for item in sales)),
            "events": len(events),
            "alarms": sum(1 for item in events if item["severity"] == "alarm"),
            "payments": [
                {
                    "label": PAYMENTS.get(key, key),
                    "count": value["count"],
                    "liters": grouped_liters(value["ml"]),
                    "amount": grouped_money(value["tiyn"]),
                }
                for key, value in by_payment.items()
            ],
            "stops": [{"label": key, "count": value} for key, value in by_stop.items()],
        },
        "events": [
            {
                "id": item["id"],
                "time": fmt_dt(item["ts"]),
                "category": item["category"],
                "severity": item["severity"],
                "code": item["code"],
                "message": item["message"],
                "sale_id": item["sale_id"],
                "source": item["source"],
            }
            for item in events
        ],
        "sales": [
            {
                "id": item["id"],
                "started": fmt_dt(item["started_at"]),
                "finished": fmt_dt(item["finished_at"]),
                "mode": item["mode"],
                "price": grouped_money(item["price_tiyn"]),
                "ordered": grouped_liters(item["ordered_ml"]) if item["ordered_ml"] is not None else "—",
                "dispensed": grouped_liters(item["dispensed_ml"]),
                "amount": grouped_money(item["amount_tiyn"]),
                "before": grouped_liters(item["totalizer_before_ml"]),
                "after": grouped_liters(item["totalizer_after_ml"]),
                "stop_reason": item["stop_reason"],
                "payment": item["payment"],
                "payment_label": PAYMENTS.get(item["payment"], item["payment"]),
            }
            for item in sales
        ],
    }


def _shift_from_query(app: App, query: dict[str, list[str]]) -> dict[str, Any]:
    raw = (query.get("number") or [""])[0]
    if not raw.isdigit():
        raise OperatorError("Укажите номер смены")
    shift = app.store.shift_by_number(int(raw))
    if shift is None:
        raise OperatorError(f"Смены {raw} нет")
    return shift


def _body(handler: BaseHTTPRequestHandler) -> dict[str, Any] | None:
    length = int(handler.headers.get("Content-Length") or "0")
    if length < 0 or length > MAX_BODY:
        _json(handler, 413, {"error": "Слишком большой запрос"})
        return None
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _json(handler, 400, {"error": "Нужен JSON"})
        return None
    if not isinstance(data, dict):
        _json(handler, 400, {"error": "Нужен объект JSON"})
        return None
    return data


def _json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    _bytes(handler, status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")


def _bytes(
    handler: BaseHTTPRequestHandler,
    status: int,
    payload: bytes,
    content_type: str,
    filename: str | None = None,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(payload)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    if filename:
        handler.send_header("Content-Disposition", f'attachment; filename="{filename}"')
    handler.end_headers()
    handler.wfile.write(payload)
