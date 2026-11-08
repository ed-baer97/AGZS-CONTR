"""Журнал и продажи в SQLite. Все величины — целые: миллилитры и тиыны."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shifts (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL UNIQUE,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    operator TEXT NOT NULL,
    column_no INTEGER NOT NULL,
    price_tiyn INTEGER NOT NULL,
    totalizer_open_ml INTEGER NOT NULL,
    totalizer_close_ml INTEGER,
    panel_totalizer_ml INTEGER,
    panel_shift_ml INTEGER,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    shift_id INTEGER,
    column_no INTEGER,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    source TEXT NOT NULL,
    details TEXT NOT NULL,
    sale_id INTEGER,
    fill_token TEXT
);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY,
    shift_id INTEGER NOT NULL,
    column_no INTEGER NOT NULL,
    product TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    mode TEXT NOT NULL,
    price_tiyn INTEGER NOT NULL,
    ordered_ml INTEGER,
    ordered_amount_tiyn INTEGER,
    dispensed_ml INTEGER NOT NULL,
    amount_tiyn INTEGER NOT NULL,
    totalizer_before_ml INTEGER NOT NULL,
    totalizer_after_ml INTEGER NOT NULL,
    stop_reason TEXT NOT NULL,
    payment TEXT NOT NULL,
    operator TEXT NOT NULL,
    status TEXT NOT NULL,
    uncommanded INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_shift ON events (shift_id, id);
CREATE INDEX IF NOT EXISTS sales_shift ON sales (shift_id, id);
"""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._conn.commit()

    def meta(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else str(row["value"])

    def has_shifts(self) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM shifts").fetchone()
        return int(row["n"]) > 0

    def open_shift_row(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM shifts WHERE status = 'открыта' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def shift_by_number(self, number: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM shifts WHERE number = ?", (number,)).fetchone()
        return dict(row) if row else None

    def shifts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM shifts ORDER BY number").fetchall()
        return [dict(row) for row in rows]

    def next_shift_number(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(number), 0) AS n FROM shifts").fetchone()
        return int(row["n"]) + 1

    def insert_shift(self, **fields: Any) -> dict[str, Any]:
        columns = [
            "number",
            "opened_at",
            "operator",
            "column_no",
            "price_tiyn",
            "totalizer_open_ml",
            "status",
        ]
        values = [fields[name] for name in columns]
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO shifts ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                values,
            )
            self._conn.commit()
            shift_id = int(cur.lastrowid)
        row = self._shift_id(shift_id)
        assert row is not None
        return row

    def close_shift_row(self, shift_id: int, **fields: Any) -> None:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            self._conn.execute(
                f"UPDATE shifts SET {assignments} WHERE id = ?",
                (*fields.values(), shift_id),
            )
            self._conn.commit()

    def _shift_id(self, shift_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM shifts WHERE id = ?", (shift_id,)).fetchone()
        return dict(row) if row else None

    def add_event(self, **fields: Any) -> int:
        details = fields.get("details") or {}
        payload = (
            fields["ts"],
            fields.get("shift_id"),
            fields.get("column_no"),
            fields["category"],
            fields["severity"],
            fields["code"],
            fields["message"],
            fields["source"],
            json.dumps(details, ensure_ascii=False),
            fields.get("sale_id"),
            fields.get("fill_token"),
        )
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (ts, shift_id, column_no, category, severity, code, "
                "message, source, details, sale_id, fill_token) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                payload,
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def attach_fill(self, fill_token: str, sale_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE events SET sale_id = ? WHERE fill_token = ? AND sale_id IS NULL",
                (sale_id, fill_token),
            )
            self._conn.commit()

    def add_sale(self, **fields: Any) -> int:
        columns = [
            "shift_id",
            "column_no",
            "product",
            "started_at",
            "finished_at",
            "mode",
            "price_tiyn",
            "ordered_ml",
            "ordered_amount_tiyn",
            "dispensed_ml",
            "amount_tiyn",
            "totalizer_before_ml",
            "totalizer_after_ml",
            "stop_reason",
            "payment",
            "operator",
            "status",
            "uncommanded",
        ]
        with self._lock:
            cur = self._conn.execute(
                f"INSERT INTO sales ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                [fields.get(name) for name in columns],
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def set_payment(self, sale_id: int, payment: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sales SET payment = ? WHERE id = ?",
                (payment, sale_id),
            )
            self._conn.commit()
            if cur.rowcount == 0:
                return None
            row = self._conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        return dict(row) if row else None

    def events(self, shift_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if shift_id is None:
                rows = self._conn.execute("SELECT * FROM events ORDER BY ts, id").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE shift_id = ? ORDER BY ts, id",
                    (shift_id,),
                ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["details"] = json.loads(item["details"])
            result.append(item)
        return result

    def sales(self, shift_id: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if shift_id is None:
                rows = self._conn.execute("SELECT * FROM sales ORDER BY id").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM sales WHERE shift_id = ? ORDER BY id",
                    (shift_id,),
                ).fetchall()
        return [dict(row) for row in rows]

    def sale(self, sale_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
        return dict(row) if row else None

    def dispensed_ml(self, shift_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(dispensed_ml), 0) AS n FROM sales WHERE shift_id = ?",
                (shift_id,),
            ).fetchone()
        return int(row["n"])

    def amount_tiyn(self, shift_id: int) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(amount_tiyn), 0) AS n FROM sales WHERE shift_id = ?",
                (shift_id,),
            ).fetchone()
        return int(row["n"])
