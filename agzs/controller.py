"""Смена, журнал и продажи поверх кадров УЗГС-01.

Колонка сама ленту событий не хранит. Здесь она собирается из ответов
на команды и из тех цифр, которые оператор переписывает с табло.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any

from agzs.dispenser import Dispenser
from agzs.format import CURRENCY, PAYMENTS, ZONE, amount_tiyn, fmt_liters, fmt_money, iso
from agzs.link import InProcessLink
from agzs.protocol import COMMANDS, PROTOCOL_ERRORS, STATUS, Frame, ProtocolError, decode_frame
from agzs.store import Store


class OperatorError(Exception):
    pass


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(ZONE)


class ManualClock:
    def __init__(self, now: datetime):
        self._now = now.astimezone(ZONE)

    def now(self) -> datetime:
        return self._now

    def set(self, moment: datetime) -> None:
        self._now = moment.astimezone(ZONE)

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)


class Controller:
    def __init__(
        self,
        link: InProcessLink | Any,
        store: Store,
        clock: SystemClock | ManualClock | None = None,
        column_no: int = 1,
        simulator: Dispenser | None = None,
    ):
        self.link = link
        self.store = store
        self.clock = clock or SystemClock()
        self.column_no = column_no
        self.sim = simulator
        self.price_tiyn = 0
        self.accounted_ml = 0
        self.shift_id: int | None = None
        self.operator = ""
        self.phase = "idle"
        self.mode: str | None = None
        self.ordered_ml: int | None = None
        self.ordered_amount_tiyn: int | None = None
        self.price_at_sale = 0
        self.pending_ml = 0
        self.totalizer_before = 0
        self.sale_started_at: datetime | None = None
        self.stop_reason = ""
        self.fill_token: str | None = None
        self.uncommanded = False
        self.last_code = "05"
        self.last_volume = 0

    def restore(self) -> None:
        self.accounted_ml = int(self.store.meta("accounted_ml") or "0")
        self.price_tiyn = int(self.store.meta("price_tiyn") or "0")
        if self.sim is not None:
            self.sim.price_tiyn = self.price_tiyn
            self.sim.totalizer_ml = self.accounted_ml
            self.sim.code = "05"
        shift = self.store.open_shift_row()
        if shift:
            self.shift_id = int(shift["id"])
            self.operator = str(shift["operator"])
            self.column_no = int(shift["column_no"])
        self.phase = "idle"
        self.last_code = "05"
        self.last_volume = 0

    def open_shift(
        self,
        operator: str,
        price_tiyn: int,
        totalizer_ml: int,
        number: int | None = None,
    ) -> dict[str, Any]:
        operator = _clean(operator, 80) or "оператор"
        if self.store.open_shift_row() is not None:
            raise OperatorError("Уже есть открытая смена")
        if self.phase != "idle":
            raise OperatorError("Сначала сбросьте колонку")
        if not 1 <= price_tiyn <= 999_999:
            raise OperatorError("Цена должна быть от 0,01 до 9999,99 тенге")
        if totalizer_ml < 0:
            raise OperatorError("Суммарник не может быть отрицательным")
        self.accounted_ml = totalizer_ml
        if self.sim is not None:
            self.sim.totalizer_ml = totalizer_ml
        self._set_price(price_tiyn)
        number = number or self.store.next_shift_number()
        shift = self.store.insert_shift(
            number=number,
            opened_at=iso(self.clock.now()),
            operator=operator,
            column_no=self.column_no,
            price_tiyn=price_tiyn,
            totalizer_open_ml=totalizer_ml,
            status="открыта",
        )
        self.shift_id = int(shift["id"])
        self.operator = operator
        self._persist()
        self._event(
            category="смена",
            severity="info",
            code="SHIFT_OPEN",
            source="panel",
            message=(
                f"Смена {number} открыта. Оператор: {operator}. "
                f"Стартовый суммарник принят с табло (клавиши 9 0 2): {fmt_liters(totalizer_ml)} л. "
                "Протокол это число не передаёт."
            ),
        )
        self._event(
            category="цена",
            severity="info",
            code="PRICE",
            source="protocol",
            message=f"Цена {fmt_money(price_tiyn)} {CURRENCY} за литр записана в колонку.",
            details={"price_tiyn": price_tiyn},
        )
        resp = self._exchange("4")
        self._mark(resp)
        self._event(
            category="протокол",
            severity="info",
            code="LINK",
            source="protocol",
            message=(
                f"Связь есть. Адрес {self.column_no:02d}, 9600 8N1, ответ на «Тест»: "
                f"{STATUS.get(resp.code, resp.code)}."
            ),
            details=_frame_details(resp, "дозы нет" if resp.code == "05" else "остаток дозы"),
        )
        return shift

    def close_shift(self, panel_totalizer_ml: int, panel_shift_ml: int) -> dict[str, Any]:
        shift = self._need_shift()
        if self.phase != "idle":
            raise OperatorError("Сначала сбросьте колонку: есть незакрытая заправка")
        if panel_totalizer_ml < 0 or panel_shift_ml < 0:
            raise OperatorError("Показания с табло не могут быть отрицательными")
        sold = self.store.dispensed_ml(int(shift["id"]))
        money = self.store.amount_tiyn(int(shift["id"]))
        total_ok = panel_totalizer_ml == self.accounted_ml
        shift_ok = panel_shift_ml == sold
        self.store.close_shift_row(
            int(shift["id"]),
            closed_at=iso(self.clock.now()),
            status="закрыта",
            totalizer_close_ml=self.accounted_ml,
            panel_totalizer_ml=panel_totalizer_ml,
            panel_shift_ml=panel_shift_ml,
        )
        if total_ok and shift_ok:
            text = (
                f"Сверка сошлась. Учётный суммарник {fmt_liters(self.accounted_ml)} л "
                f"совпал с табло (9 0 2). Счётчик смены {fmt_liters(sold)} л совпал с табло (9 0 1)."
            )
            severity = "info"
        else:
            bits = []
            if not total_ok:
                bits.append(
                    f"суммарник: табло {fmt_liters(panel_totalizer_ml)} л, учёт {fmt_liters(self.accounted_ml)} л"
                )
            if not shift_ok:
                bits.append(
                    f"смена: табло {fmt_liters(panel_shift_ml)} л, продажи {fmt_liters(sold)} л"
                )
            text = "Сверка не сошлась. " + "; ".join(bits) + "."
            severity = "warning"
        self._event(
            category="счётчик",
            severity=severity,
            code="RECONCILE",
            source="panel",
            message=text,
            details={
                "panel_totalizer_ml": panel_totalizer_ml,
                "accounted_ml": self.accounted_ml,
                "panel_shift_ml": panel_shift_ml,
                "sold_ml": sold,
            },
        )
        count = len(self.store.sales(int(shift["id"])))
        self._event(
            category="смена",
            severity="info",
            code="SHIFT_CLOSE",
            source="controller",
            message=(
                f"Смена {shift['number']} закрыта. Чеков {count}, "
                f"отпущено {fmt_liters(sold)} л на {fmt_money(money)} {CURRENCY}."
            ),
        )
        self.shift_id = None
        closed = self.store.shift_by_number(int(shift["number"]))
        assert closed is not None
        return closed

    def panel_note(self, kind: str, text: str) -> None:
        self._need_shift()
        labels = {
            "totalizer": "Суммарный счётчик (9 0 2)",
            "shift_counter": "Счётчик за смену (9 0 1)",
            "last_fill": "Прошлая заправка (9 0 5)",
            "temperature": "Температура СУГ",
            "buttons": "Кнопки колонки",
            "error": "Код ошибки с табло",
            "valve": "Клапан",
            "other": "Заметка оператора",
        }
        if kind not in labels:
            raise OperatorError("Неизвестный тип заметки с табло")
        text = _clean(text, 500)
        if not text:
            raise OperatorError("Пустая заметка")
        severity = "alarm" if kind == "error" else "info"
        self._event(
            category="табло",
            severity=severity,
            code="PANEL",
            source="panel",
            message=f"{labels[kind]}: {text}",
            details={"kind": kind},
        )

    def set_payment(self, sale_id: int, payment: str) -> dict[str, Any]:
        if payment not in PAYMENTS:
            raise OperatorError("Оплата: наличные, карта, ведомость, талон или не указано")
        sale = self.store.set_payment(sale_id, payment)
        if sale is None:
            raise OperatorError("Такой продажи нет")
        self._event(
            category="отпуск",
            severity="info",
            code="PAYMENT",
            source="operator",
            sale_id=sale_id,
            shift_id=int(sale["shift_id"]),
            message=f"Оплата продажи {sale_id}: {PAYMENTS[payment]}. Колонка вид оплаты не передаёт.",
        )
        return sale

    def load_liters(self, ml: int) -> None:
        self._load(ml, "литры", amount_tiyn(ml, self.price_tiyn))

    def load_amount(self, tiyn: int) -> None:
        if self.price_tiyn <= 0:
            raise OperatorError("Сначала задайте цену")
        if tiyn <= 0:
            raise OperatorError("Сумма должна быть больше нуля")
        ml = (tiyn * 1000) // self.price_tiyn
        if ml <= 0 or ml > 999_000:
            raise OperatorError("Сумма не пересчитывается в дозу от 0,001 до 999,000 л")
        self._load(ml, "сумма", tiyn)
        actual = amount_tiyn(ml, self.price_tiyn)
        if actual != tiyn:
            self._event(
                category="отпуск",
                severity="info",
                code="DOSE",
                source="controller",
                message=(
                    f"Сумма {fmt_money(tiyn)} {CURRENCY} на цену {fmt_money(self.price_tiyn)} "
                    f"не делится точно. В колонку загружено {fmt_liters(ml)} л, "
                    f"это {fmt_money(actual)} {CURRENCY}."
                ),
            )

    def load_full(self) -> None:
        self._require_idle()
        if self.price_tiyn <= 0:
            raise OperatorError("Сначала задайте цену")
        resp = self._exchange("9", volume=0)
        if resp.error != "00":
            self._reject("9", resp)
            raise OperatorError("Колонка не приняла режим «до полного бака»")
        self.mode = "до полного"
        self.ordered_ml = None
        self.ordered_amount_tiyn = None
        self.price_at_sale = resp.price_tiyn
        self.phase = "armed"
        self._arm()
        self._mark(resp)
        self._event(
            category="отпуск",
            severity="info",
            code="DOSE",
            source="protocol",
            message=(
                f"Режим «до полного бака», цена {fmt_money(resp.price_tiyn)} {CURRENCY}/л. "
                "Доза в кадр не кладётся: объём станет известен по окончании."
            ),
            details=_frame_details(resp, "дозы нет"),
        )

    def start(self) -> None:
        continuing = self.sale_started_at is not None
        resp = self._exchange("5")
        if resp.error != "00":
            self._reject("5", resp)
            raise OperatorError("Колонка не приняла пуск")
        self._arm()
        self._mark_started(resp, uncommanded=False)
        self.phase = "running"
        self._mark(resp)
        meaning = _meaning(self.mode, resp.code)
        if continuing:
            message = "Пуск продолжен после останова. Это та же заправка, чек ещё не закрыт."
        else:
            message = f"Пуск. В кадре {fmt_liters(resp.volume_ml)} л ({meaning})."
        self._event(
            category="отпуск",
            severity="info",
            code="START",
            source="protocol",
            message=message,
            details=_frame_details(resp, meaning),
        )

    def stop(self) -> None:
        resp = self._exchange("6")
        if resp.error != "00":
            self._reject("6", resp)
            raise OperatorError("Колонка не приняла останов")
        if self.last_code == "03":
            self._accumulate(resp)
        self._note_stop(resp, "command")
        self._mark(resp)

    def reset(self) -> int | None:
        if self.phase == "running":
            self.poll()
        resp = self._exchange("7")
        if resp.error != "00":
            self._reject("7", resp)
            raise OperatorError("Колонка не приняла сброс")
        sale_id = self._close_fill()
        self.phase = "idle"
        self.mode = None
        self._mark(resp)
        return sale_id

    def poll(self) -> Frame:
        resp = self._exchange("4")
        if resp.error != "00":
            self._reject("4", resp)
            return resp
        self._observe(resp)
        return resp

    def poll_with_bad_checksum_once(self) -> Frame:
        """Имитация битого кадра: колонка ответила, контрольная сумма не сошлась, запрос повторён."""
        probe = self._exchange("4")
        raw = bytearray(probe.encode())
        raw[-1] ^= 0xFF
        try:
            decode_frame(bytes(raw))
        except ProtocolError:
            self._event(
                category="протокол",
                severity="warning",
                code="E227",
                source="protocol",
                message="Ошибка контрольной суммы кадра. Запрос «Тест» повторён.",
            )
        else:
            raise OperatorError("Контрольная сумма неожиданно сошлась")
        return self.poll()

    def perform_fill(
        self,
        mode: str,
        *,
        liters_ml: int | None = None,
        amount_tiyn_value: int | None = None,
        tank_ml: int | None = None,
        payment: str = "unknown",
    ) -> int:
        if self.sim is None:
            raise OperatorError(
                "На живой колонке отпуск включается пистолетом и кнопкой. "
                "Программа в этом режиме только опрашивает кадры."
            )
        if mode == "литры":
            if not liters_ml:
                raise OperatorError("Нужны литры")
            self.load_liters(liters_ml)
            target = liters_ml
        elif mode == "сумма":
            if not amount_tiyn_value:
                raise OperatorError("Нужна сумма")
            self.load_amount(amount_tiyn_value)
            target = int(self.ordered_ml or 0)
        elif mode == "до полного":
            if not tank_ml:
                raise OperatorError("Имитатору нужен объём до отсечки, в литрах")
            self.load_full()
            self.sim.set_full_target(tank_ml)
            target = tank_ml
        else:
            raise OperatorError("Режим: литры, сумма или до полного")
        self.start()
        self.sim.pump(target)
        self.poll()
        sale_id = self.reset()
        if sale_id is None:
            raise OperatorError("Заправка прошла без отпуска")
        if payment != "unknown":
            self.set_payment(sale_id, payment)
        return sale_id

    def _load(self, ml: int, mode: str, ordered_amount: int) -> None:
        self._require_idle()
        if ml <= 0 or ml > 999_000:
            raise OperatorError("Доза должна быть от 0,001 до 999,000 л. На 999 л колонка даёт ошибку 03.")
        resp = self._exchange("1", volume=ml)
        if resp.error != "00":
            self._reject("1", resp)
            raise OperatorError("Колонка не приняла дозу")
        self.mode = mode
        self.ordered_ml = ml
        self.ordered_amount_tiyn = ordered_amount
        self.price_at_sale = resp.price_tiyn
        self.phase = "armed"
        self._arm()
        self._mark(resp)
        if mode == "сумма":
            message = (
                f"Загружена сумма {fmt_money(ordered_amount)} {CURRENCY}: "
                f"в колонку ушло {fmt_liters(ml)} л по {fmt_money(resp.price_tiyn)} {CURRENCY}/л. "
                "Кадр умеет цену и миллилитры, не сумму."
            )
        else:
            message = (
                f"Загружена доза {fmt_liters(ml)} л по {fmt_money(resp.price_tiyn)} {CURRENCY}/л. "
                "Колонка готова к пуску."
            )
        self._event(
            category="отпуск",
            severity="info",
            code="DOSE",
            source="protocol",
            message=message,
            details=_frame_details(resp, "остаток дозы"),
        )

    def _set_price(self, price_tiyn: int) -> None:
        resp = self._exchange("3", price=price_tiyn, volume=0)
        if resp.error != "00":
            self._reject("3", resp)
            raise OperatorError("Колонка не приняла цену: она ставится только из состояния «сброс»")
        self.price_tiyn = resp.price_tiyn
        self._mark(resp)

    def _observe(self, resp: Frame) -> None:
        if resp.code == "03" and self.phase in {"idle", "armed"}:
            self.mode = self.mode or "литры"
            uncommanded = self.phase == "idle"
            self._arm()
            self._mark_started(resp, uncommanded=uncommanded)
            self.phase = "running"
            self._mark(resp)
            if self.uncommanded:
                message = (
                    "Пуск без команды системы управления. Отпуск виден только потому, "
                    "что состояние стало «пуск»."
                )
                severity = "warning"
                code = "UNCOMMAND"
            else:
                message = "Пуск с колонки по уже загруженной дозе."
                severity = "info"
                code = "START"
            self._event(
                category="отпуск",
                severity=severity,
                code=code,
                source="protocol",
                message=message,
                details=_frame_details(resp, _meaning(self.mode, resp.code)),
            )
            return
        if self.last_code == "03" and resp.code == "03":
            self._accumulate(resp)
            self._mark(resp)
            return
        if self.last_code == "03" and resp.code != "03":
            self._accumulate(resp)
            self._note_stop(resp, "poll")
            self._mark(resp)
            return
        if resp.code != self.last_code:
            self._event(
                category="состояние",
                severity="alarm" if resp.code == "06" else "info",
                code="STATE",
                source="protocol",
                message=f"Состояние колонки: {STATUS.get(resp.code, resp.code)}.",
                details=_frame_details(resp, _meaning(self.mode, resp.code)),
            )
        self._mark(resp)

    def _arm(self) -> None:
        if self.fill_token is None:
            self.fill_token = secrets.token_hex(8)
            self.pending_ml = 0
            self.stop_reason = ""
            self.uncommanded = False
            self.sale_started_at = None

    def _mark_started(self, resp: Frame, uncommanded: bool) -> None:
        if self.sale_started_at is None:
            self.sale_started_at = self.clock.now()
            self.totalizer_before = self.accounted_ml
            self.price_at_sale = resp.price_tiyn or self.price_tiyn
        if uncommanded:
            self.uncommanded = True

    def _accumulate(self, resp: Frame) -> None:
        meaning = _meaning(self.mode, resp.code)
        if self.mode == "до полного" or (self.uncommanded and resp.volume_ml > self.last_volume and self.last_code == "03"):
            delta = resp.volume_ml - self.last_volume
            used_meaning = "отпущенный объём"
        else:
            delta = self.last_volume - resp.volume_ml
            used_meaning = meaning
        if delta <= 0:
            return
        self.pending_ml += delta
        self.accounted_ml += delta
        self._persist()
        self._event(
            category="отпуск",
            severity="info",
            code="FLOW",
            source="protocol",
            message=(
                f"Отпущено {fmt_liters(delta)} л. В кадре {fmt_liters(resp.volume_ml)} л "
                f"({used_meaning}). С начала заправки {fmt_liters(self.pending_ml)} л."
            ),
            details=_frame_details(resp, used_meaning) | {"delta_ml": delta},
        )

    def _note_stop(self, resp: Frame, cause: str) -> None:
        reason, severity = _stop_reason(self.mode, resp, cause)
        self.stop_reason = reason
        self.phase = "paused"
        meaning = _meaning(self.mode, resp.code)
        if reason == "авария":
            message = (
                f"Останов: авария, код 06. Номер ошибки с табло (01–04) в кадр не входит. "
                f"Отпущено {fmt_liters(self.pending_ml)} л, в кадре {fmt_liters(resp.volume_ml)} л "
                f"({meaning}). Чек закроется по сбросу показаний."
            )
        else:
            head = reason[:1].upper() + reason[1:]
            message = (
                f"{head}. Отпущено {fmt_liters(self.pending_ml)} л, "
                f"в кадре {fmt_liters(resp.volume_ml)} л ({meaning}). "
                "Чек закроется по сбросу показаний."
            )
        self._event(
            category="авария" if reason == "авария" else "отпуск",
            severity=severity,
            code="ALARM" if reason == "авария" else "STOP",
            source="protocol",
            message=message,
            details=_frame_details(resp, meaning) | {"stop_reason": reason, "cause": cause},
        )

    def _close_fill(self) -> int | None:
        token = self.fill_token
        pending = self.pending_ml
        if token is None or pending <= 0:
            self._event(
                category="отпуск",
                severity="info",
                code="RESET",
                source="protocol",
                message="Сброс показаний. Отпуска не было, продажа не создана.",
            )
            self._clear_fill()
            return None
        if not self.stop_reason:
            self.stop_reason = "сброс во время отпуска"
        money = amount_tiyn(pending, self.price_at_sale)
        after = self.accounted_ml
        if after - self.totalizer_before != pending:
            self._event(
                category="счётчик",
                severity="alarm",
                code="RECONCILE",
                source="controller",
                message=(
                    "Внутреннее расхождение: учётный суммарник разошёлся с литрами заправки. "
                    f"Разница суммарника {fmt_liters(after - self.totalizer_before)} л, "
                    f"отпуск {fmt_liters(pending)} л."
                ),
            )
        shift = self._need_shift()
        sale_id = self.store.add_sale(
            shift_id=int(shift["id"]),
            column_no=self.column_no,
            product="СУГ",
            started_at=iso(self.sale_started_at or self.clock.now()),
            finished_at=iso(self.clock.now()),
            mode=self.mode or "литры",
            price_tiyn=self.price_at_sale,
            ordered_ml=self.ordered_ml,
            ordered_amount_tiyn=self.ordered_amount_tiyn,
            dispensed_ml=pending,
            amount_tiyn=money,
            totalizer_before_ml=self.totalizer_before,
            totalizer_after_ml=after,
            stop_reason=self.stop_reason,
            payment="unknown",
            operator=self.operator,
            status="закрыта",
            uncommanded=1 if self.uncommanded else 0,
        )
        self.store.attach_fill(token, sale_id)
        self._event(
            category="отпуск",
            severity="alarm" if self.stop_reason == "авария" else "info",
            code="SALE",
            source="controller",
            sale_id=sale_id,
            message=(
                f"Сброс показаний. Продажа {sale_id}: {fmt_liters(pending)} л "
                f"на {fmt_money(money)} {CURRENCY}. Останов: {self.stop_reason}. "
                f"Учётный суммарник {fmt_liters(self.totalizer_before)} → {fmt_liters(after)} л."
            ),
        )
        self._clear_fill()
        return sale_id

    def _clear_fill(self) -> None:
        self.fill_token = None
        self.pending_ml = 0
        self.ordered_ml = None
        self.ordered_amount_tiyn = None
        self.stop_reason = ""
        self.uncommanded = False
        self.sale_started_at = None

    def _reject(self, command: str, resp: Frame) -> None:
        code = {"01": "E225", "02": "E226", "03": "E227"}.get(resp.error, "E227")
        self._event(
            category="протокол",
            severity="warning",
            code=code,
            source="protocol",
            message=(
                f"Команда «{COMMANDS.get(command, command)}» отклонена: "
                f"{PROTOCOL_ERRORS.get(resp.error, resp.error)}. "
                f"Состояние колонки: {STATUS.get(resp.code, resp.code)}."
            ),
            details=_frame_details(resp, _meaning(self.mode, resp.code)),
        )

    def _exchange(self, command: str, price: int | None = None, volume: int = 0) -> Frame:
        frame = Frame(
            self.column_no,
            command,
            self.price_tiyn if price is None else price,
            volume,
            "00",
            "00",
        )
        try:
            resp = self.link.exchange(frame)
        except ProtocolError as exc:
            self._event(
                category="протокол",
                severity="alarm",
                code="E227",
                source="protocol",
                message=f"Обмен сорвался: {exc}",
            )
            raise OperatorError(str(exc)) from exc
        if resp is None:
            raise OperatorError("Колонка не ответила")
        return resp

    def _mark(self, resp: Frame) -> None:
        self.last_code = resp.code
        self.last_volume = resp.volume_ml

    def _require_idle(self) -> None:
        self._need_shift()
        if self.phase != "idle":
            raise OperatorError("Колонка занята. Сначала сброс показаний.")

    def _need_shift(self) -> dict[str, Any]:
        shift = self.store.open_shift_row()
        if shift is None or self.shift_id is None:
            raise OperatorError("Нет открытой смены")
        return shift

    def _persist(self) -> None:
        self.store.set_meta("accounted_ml", str(self.accounted_ml))
        self.store.set_meta("price_tiyn", str(self.price_tiyn))

    def _event(
        self,
        *,
        category: str,
        severity: str,
        code: str,
        source: str,
        message: str,
        details: dict[str, Any] | None = None,
        sale_id: int | None = None,
        shift_id: int | None = None,
    ) -> None:
        self.store.add_event(
            ts=iso(self.clock.now()),
            shift_id=self.shift_id if shift_id is None else shift_id,
            column_no=self.column_no,
            category=category,
            severity=severity,
            code=code,
            message=message,
            source=source,
            details=details or {},
            sale_id=sale_id,
            fill_token=self.fill_token,
        )


def _meaning(mode: str | None, code: str) -> str:
    if mode == "до полного" and code in {"03", "06", "07"}:
        return "отпущенный объём"
    if code == "05":
        return "дозы нет"
    return "остаток дозы"


def _stop_reason(mode: str | None, resp: Frame, cause: str) -> tuple[str, str]:
    if resp.code == "06":
        return "авария", "alarm"
    if mode == "до полного" and resp.code == "07":
        return "бак заполнен", "info"
    if resp.code == "04" and resp.volume_ml == 0 and mode != "до полного":
        return "доза набрана", "info"
    if cause == "command":
        return "останов оператором", "info"
    if resp.code == "04":
        return "останов колонкой", "warning"
    return "останов", "info"


def _frame_details(resp: Frame, meaning: str) -> dict[str, Any]:
    return {
        "command": resp.command,
        "error": resp.error,
        "code": resp.code,
        "price_tiyn": resp.price_tiyn,
        "volume_ml": resp.volume_ml,
        "volume_meaning": meaning,
    }


def _clean(text: str, limit: int) -> str:
    cleaned = "".join(ch for ch in str(text) if ch >= " " or ch == "\n")
    cleaned = " ".join(cleaned.split())
    return cleaned[:limit].strip()
