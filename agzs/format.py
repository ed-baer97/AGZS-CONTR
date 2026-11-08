"""Числа, время и подписи. Колонка валюту не передаёт."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Qyzylorda")
CURRENCY = "тенге"

PAYMENTS = {
    "cash": "наличные",
    "card": "карта",
    "invoice": "ведомость",
    "coupon": "талон",
    "unknown": "не указано",
}


def iso(dt: datetime) -> str:
    return dt.astimezone(ZONE).isoformat(timespec="seconds")


def fmt_dt(value: datetime | str | None) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    local = value.astimezone(ZONE)
    return local.strftime("%d.%m.%Y %H:%M:%S +05")


def fmt_liters(ml: int | None) -> str:
    if ml is None:
        return ""
    sign = "-" if ml < 0 else ""
    whole, frac = divmod(abs(int(ml)), 1000)
    return f"{sign}{whole},{frac:03d}"


def fmt_money(tiyn: int | None) -> str:
    if tiyn is None:
        return ""
    sign = "-" if tiyn < 0 else ""
    whole, frac = divmod(abs(int(tiyn)), 100)
    return f"{sign}{whole},{frac:02d}"


def grouped_liters(ml: int | None) -> str:
    text = fmt_liters(ml)
    if not text:
        return ""
    sign = "-" if text.startswith("-") else ""
    body = text[1:] if sign else text
    whole, frac = body.split(",")
    return f"{sign}{_groups(whole)},{frac}"


def grouped_money(tiyn: int | None) -> str:
    text = fmt_money(tiyn)
    if not text:
        return ""
    sign = "-" if text.startswith("-") else ""
    body = text[1:] if sign else text
    whole, frac = body.split(",")
    return f"{sign}{_groups(whole)},{frac}"


def _groups(whole: str) -> str:
    parts: list[str] = []
    while whole:
        parts.append(whole[-3:])
        whole = whole[:-3]
    return " ".join(reversed(parts))


def amount_tiyn(ml: int, price_tiyn: int) -> int:
    """Сумма в тиынах. 1 л = 1000 мл, 1 тенге = 100 тиынов."""
    return (int(ml) * int(price_tiyn) + 500) // 1000


def parse_scaled(text: str, scale: int) -> int:
    """'20,5' при scale=3 -> 20500. Лишние знаки после запятой не режем молча."""
    raw = str(text).strip().replace(" ", "").replace("\u00a0", "")
    if not raw:
        raise ValueError("Пустое число")
    raw = raw.replace(",", ".")
    if raw.count(".") > 1 or any(ch not in "0123456789." for ch in raw):
        raise ValueError(f"Не число: {text}")
    if "." in raw:
        whole, frac = raw.split(".")
        if len(frac) > scale:
            raise ValueError(f"Слишком много знаков после запятой: {text}")
        frac = frac.ljust(scale, "0")
    else:
        whole, frac = raw, "0" * scale
    if whole == "":
        whole = "0"
    return int(whole) * (10**scale) + int(frac)


def parse_liters(text: str) -> int:
    return parse_scaled(text, 3)


def parse_money(text: str) -> int:
    return parse_scaled(text, 2)
