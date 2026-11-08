"""Подписи и цифры пульта колонок. Виджеты Qt здесь не создаются."""

from agzs_shared.money import cost_kzt_test_half_up

_CAPTIONS = {
    "IDLE": "СВОБОДНА",
    "RESERVED": "ДОЗА",
    "PAYMENT_PENDING": "ОЖИДАНИЕ",
    "READY": "ПУСК",
    "DISPENSING": "НАЛИВ",
    "COMPLETED": "ОТПУЩЕНО",
    "STOPPED": "СТОП",
    "ERROR": "АВАРИЯ",
    "MAINTENANCE": "РЕМОНТ",
}

_LAMPS = {
    "СВОБОДНА": "#2f9e4f",
    "ДОЗА": "#e0a106",
    "ОЖИДАНИЕ": "#e0a106",
    "ПУСК": "#e0a106",
    "НАЛИВ": "#1ecb4a",
    "ОТПУЩЕНО": "#3d7ec9",
    "СТОП": "#3d7ec9",
    "АВАРИЯ": "#d64545",
    "РЕМОНТ": "#e07a2f",
    "ОТКЛЮЧЕНА": "#8b95a1",
    "НЕТ СВЯЗИ": "#8b95a1",
}


def status_caption(status: str, observation_quality: str) -> str:
    if observation_quality != "CONFIRMED":
        return "НЕТ СВЯЗИ"
    if status == "OFFLINE":
        return "ОТКЛЮЧЕНА"
    return _CAPTIONS.get(status, status)


def lamp_color(caption: str) -> str:
    return _LAMPS.get(caption, "#8b95a1")


def format_money(kzt: int | None, *, hidden: bool) -> str:
    if hidden or kzt is None:
        return "------"
    if kzt < 0:
        kzt = 0
    if kzt > 999999:
        kzt = 999999
    return f"{kzt:06d}"


def format_volume(ml: int | None, *, hidden: bool) -> str:
    if hidden or ml is None:
        return "----.--"
    if ml < 0:
        ml = 0
    if ml > 9_999_990:
        ml = 9_999_990
    whole = ml // 1000
    hundredths = (ml % 1000) // 10
    return f"{whole:04d}.{hundredths:02d}"


def preset_kzt(buffer: str, mode: str, price_kzt_per_liter: int) -> int:
    """Перевод набранной дозы в целые тенге. Литры считаются по цене колонки."""

    text = buffer.strip().replace(",", ".")
    if not text or text == ".":
        raise ValueError("Введите дозу")
    if mode == "money":
        if not text.isdigit():
            raise ValueError("Сумма задаётся целыми тенге")
        amount = int(text)
        if amount <= 0:
            raise ValueError("Сумма дозы должна быть больше нуля")
        return amount
    if mode != "volume":
        raise ValueError("Неизвестный режим дозы")
    ml = liters_to_ml(text)
    if ml <= 0:
        raise ValueError("Объём дозы должен быть больше нуля")
    amount = cost_kzt_test_half_up(ml, price_kzt_per_liter)
    if amount <= 0:
        raise ValueError("По этой цене объём не даёт даже одного тенге")
    return amount


def liters_to_ml(text: str) -> int:
    if text.count(".") > 1:
        raise ValueError("Объём задаётся в литрах")
    if "." in text:
        whole, frac = text.split(".")
        if whole and not whole.isdigit():
            raise ValueError("Объём задаётся в литрах")
        if not frac.isdigit() or len(frac) > 2:
            raise ValueError("У объёма не больше двух знаков после запятой")
        hundredths = int(frac.ljust(2, "0")) if frac else 0
        return int(whole or "0") * 1000 + hundredths * 10
    if not text.isdigit():
        raise ValueError("Объём задаётся в литрах")
    return int(text) * 1000


def face_digits(
    *,
    status: str,
    observation_quality: str,
    dose_ml: int | None,
    provisional_kzt: int | None,
    remembered_ml: int | None,
    remembered_kzt: int | None,
) -> tuple[str, str, int | None, int | None]:
    """Цифры суммы и дозы. Последний отпуск остаётся на табло, пока его не сбросят."""

    hidden = observation_quality != "CONFIRMED"
    if hidden:
        return "------", "----.--", remembered_ml, remembered_kzt
    if dose_ml is not None:
        money = provisional_kzt if provisional_kzt is not None else remembered_kzt
        if money is None:
            money = 0
        return format_money(money, hidden=False), format_volume(dose_ml, hidden=False), dose_ml, money
    if remembered_ml is not None and status in {"IDLE", "COMPLETED", "STOPPED"}:
        return (
            format_money(remembered_kzt or 0, hidden=False),
            format_volume(remembered_ml, hidden=False),
            remembered_ml,
            remembered_kzt,
        )
    return format_money(0, hidden=False), format_volume(0, hidden=False), remembered_ml, remembered_kzt
