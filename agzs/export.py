"""Выгрузка журнала и продаж. Десятичная запятая, разделитель «;», время +05."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any

from agzs.format import CURRENCY, PAYMENTS, ZONE, fmt_dt, fmt_liters, fmt_money
from agzs.store import Store

SOURCE = "Искра 1.72, суммарник учётный"


def sales_csv(store: Store, shift: dict[str, Any]) -> bytes:
    rows = store.sales(int(shift["id"]))
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        [
            "Номер",
            "Смена",
            "Колонка",
            "Продукт",
            "Начало",
            "Окончание",
            "Длительность, с",
            "Режим",
            "Цена, тенге/л",
            "Заказано, л",
            "Заказано, тенге",
            "Отпущено, л",
            "Сумма, тенге",
            "Суммарник до, л",
            "Суммарник после, л",
            "Причина останова",
            "Оплата",
            "Оператор",
            "Пуск без команды СУ",
            "Источник",
        ]
    )
    for sale in rows:
        writer.writerow(
            [
                sale["id"],
                shift["number"],
                sale["column_no"],
                sale["product"],
                fmt_dt(sale["started_at"]),
                fmt_dt(sale["finished_at"]),
                _duration(sale),
                sale["mode"],
                fmt_money(sale["price_tiyn"]),
                fmt_liters(sale["ordered_ml"]) if sale["ordered_ml"] is not None else "",
                fmt_money(sale["ordered_amount_tiyn"]) if sale["ordered_amount_tiyn"] is not None else "",
                fmt_liters(sale["dispensed_ml"]),
                fmt_money(sale["amount_tiyn"]),
                fmt_liters(sale["totalizer_before_ml"]),
                fmt_liters(sale["totalizer_after_ml"]),
                sale["stop_reason"],
                PAYMENTS.get(sale["payment"], sale["payment"]),
                sale["operator"],
                "да" if sale["uncommanded"] else "нет",
                SOURCE,
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def events_csv(store: Store, shift: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["Номер", "Время", "Смена", "Колонка", "Категория", "Важность", "Код", "Событие", "Продажа", "Источник"]
    )
    for event in store.events(int(shift["id"])):
        writer.writerow(
            [
                event["id"],
                fmt_dt(event["ts"]),
                shift["number"],
                event["column_no"] or "",
                event["category"],
                event["severity"],
                event["code"],
                event["message"],
                event["sale_id"] or "",
                event["source"],
            ]
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def events_text(store: Store, shift: dict[str, Any]) -> str:
    sales = store.sales(int(shift["id"]))
    liters = sum(int(item["dispensed_ml"]) for item in sales)
    money = sum(int(item["amount_tiyn"]) for item in sales)
    lines = [
        "ЖУРНАЛ СОБЫТИЙ",
        f"Колонка УЗГС-01 / УЗСГ-01 №{shift['column_no']}, смена {shift['number']}",
        f"Оператор: {shift['operator']}",
        f"Открыта: {fmt_dt(shift['opened_at'])}",
        f"Закрыта: {fmt_dt(shift['closed_at']) or 'ещё открыта'}",
        "Часовой пояс: Asia/Qyzylorda (UTC+5)",
        "",
        "Колонка сама ленту не хранит. Строки ниже собраны из кадров Искра 1.72",
        "и из цифр, которые оператор переписал с табло. Суммарник в продажах учётный:",
        "протокол свой накопитель не отдаёт, его смотрят клавишами 9 0 2.",
        f"Валюта в суммах — {CURRENCY}: в кадре только число цены.",
        "",
        f"Чеков: {len(sales)}. Отпущено: {fmt_liters(liters)} л. Сумма: {fmt_money(money)} {CURRENCY}.",
        "",
    ]
    for event in store.events(int(shift["id"])):
        sale = f"  продажа {event['sale_id']}" if event["sale_id"] else ""
        lines.append(
            f"{fmt_dt(event['ts'])}  {event['severity']:<7}  {event['category']:<10}  "
            f"{event['message']}{sale}"
        )
    lines.append("")
    return "\n".join(lines)


def sales_json(store: Store, shift: dict[str, Any]) -> bytes:
    sales = store.sales(int(shift["id"]))
    payload = {
        "timezone": "Asia/Qyzylorda",
        "utc_offset": "+05:00",
        "currency": CURRENCY,
        "product_note": "СУГ записан программой: колонка марку не передаёт.",
        "totalizer_note": (
            "Суммарник учётный: программа складывает отпущенный объём. "
            "Штатный счётчик УЗГС-01 в протокол Искра 1.72 не входит, его списывают с табло (9 0 2)."
        ),
        "source": SOURCE,
        "shift": {
            "number": shift["number"],
            "operator": shift["operator"],
            "column_no": shift["column_no"],
            "status": shift["status"],
            "opened_at": shift["opened_at"],
            "closed_at": shift["closed_at"],
            "price_per_liter": fmt_money(shift["price_tiyn"]),
            "totalizer_open_l": fmt_liters(shift["totalizer_open_ml"]),
            "totalizer_close_l": fmt_liters(shift["totalizer_close_ml"]),
            "panel_totalizer_l": fmt_liters(shift["panel_totalizer_ml"]),
            "panel_shift_l": fmt_liters(shift["panel_shift_ml"]),
        },
        "totals": {
            "receipts": len(sales),
            "liters": fmt_liters(sum(int(item["dispensed_ml"]) for item in sales)),
            "amount": fmt_money(sum(int(item["amount_tiyn"]) for item in sales)),
        },
        "sales": [_sale_json(shift, sale) for sale in sales],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"


def _sale_json(shift: dict[str, Any], sale: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": sale["id"],
        "shift": shift["number"],
        "column": sale["column_no"],
        "product": sale["product"],
        "started_at": sale["started_at"],
        "finished_at": sale["finished_at"],
        "started_at_local": fmt_dt(sale["started_at"]),
        "finished_at_local": fmt_dt(sale["finished_at"]),
        "duration_s": _duration(sale),
        "mode": sale["mode"],
        "price_per_liter": fmt_money(sale["price_tiyn"]),
        "ordered_l": fmt_liters(sale["ordered_ml"]) if sale["ordered_ml"] is not None else None,
        "ordered_amount": fmt_money(sale["ordered_amount_tiyn"]) if sale["ordered_amount_tiyn"] is not None else None,
        "dispensed_l": fmt_liters(sale["dispensed_ml"]),
        "amount": fmt_money(sale["amount_tiyn"]),
        "accounted_totalizer_before_l": fmt_liters(sale["totalizer_before_ml"]),
        "accounted_totalizer_after_l": fmt_liters(sale["totalizer_after_ml"]),
        "stop_reason": sale["stop_reason"],
        "payment": PAYMENTS.get(sale["payment"], sale["payment"]),
        "operator": sale["operator"],
        "uncommanded": bool(sale["uncommanded"]),
        "source": SOURCE,
    }


def _duration(sale: dict[str, Any]) -> int:
    start = datetime.fromisoformat(sale["started_at"]).astimezone(ZONE)
    end = datetime.fromisoformat(sale["finished_at"]).astimezone(ZONE)
    return int((end - start).total_seconds())


def write_shift_exports(store: Store, directory: str, number: int) -> list[str]:
    from pathlib import Path

    shift = store.shift_by_number(number)
    if shift is None:
        raise SystemExit(f"Смены {number} нет")
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        f"zhurnal-smena-{number}.csv": events_csv(store, shift),
        f"zhurnal-smena-{number}.txt": events_text(store, shift).encode("utf-8"),
        f"prodazhi-smena-{number}.csv": sales_csv(store, shift),
        f"prodazhi-smena-{number}.json": sales_json(store, shift),
    }
    written = []
    for name, payload in files.items():
        path = folder / name
        path.write_bytes(payload)
        written.append(str(path))
    return written
