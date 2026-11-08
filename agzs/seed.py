"""Учебная смена 14 за 8 ноября 2026, пояс Кызылорда.

Цифры построены тем же путём, что и живая заправка на имитаторе:
кадр дозы, пуск, отпуск, останов, сброс. Это не выгрузка с подключённой колонки.
"""

from __future__ import annotations

from datetime import datetime

from agzs.controller import Controller, ManualClock
from agzs.dispenser import Dispenser
from agzs.format import ZONE, fmt_liters
from agzs.link import InProcessLink
from agzs.store import Store

OPEN_TOTALIZER_ML = 125_430_500
PRICE_TIYN = 9_000
OPERATOR = "оператор"


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 11, 8, hour, minute, second, tzinfo=ZONE)


def seed_demo(store: Store) -> None:
    if store.has_shifts():
        return
    clock = ManualClock(at(8, 0, 0))
    dispenser = Dispenser(number=1)
    ctrl = Controller(
        InProcessLink(dispenser),
        store,
        clock=clock,
        column_no=1,
        simulator=dispenser,
    )
    ctrl.open_shift(OPERATOR, PRICE_TIYN, OPEN_TOTALIZER_ML, number=14)
    clock.advance(20)
    ctrl.panel_note(
        "other",
        "Учебная смена. Колонка не подключена: кадры построил имитатор УЗГС-01 по протоколу Искра 1.72.",
    )
    clock.advance(20)
    ctrl.panel_note("temperature", "+12 °C после сброса, строка пульта")
    clock.advance(15)
    ctrl.panel_note("buttons", "ВКЛ")
    clock.advance(15)
    ctrl.panel_note("shift_counter", "0,000 л, обнулён в конце прошлой смены клавишами 9 0 1 0")
    clock.advance(15)
    ctrl.panel_note("valve", "светодиод «клапан» погашен, шланг без вздутий")

    def dispense(
        moment: datetime,
        payment: str,
        kind: str,
        *,
        outcome: str = "complete",
        ordered_ml: int | None = None,
        amount_tiyn: int | None = None,
        target_ml: int | None = None,
        delivered_ml: int | None = None,
        pump_s: int = 90,
    ) -> int:
        sim = ctrl.sim
        if sim is None:
            raise RuntimeError("учебная смена собирается только на имитаторе")
        clock.set(moment)
        if kind == "liters":
            ctrl.load_liters(int(ordered_ml))
        elif kind == "amount":
            ctrl.load_amount(int(amount_tiyn))
        elif kind == "full":
            ctrl.load_full()
            sim.set_full_target(int(target_ml))
        else:
            raise RuntimeError(kind)
        clock.advance(8)
        ctrl.start()
        clock.advance(pump_s)
        if outcome == "complete":
            sim.pump(999_000)
            ctrl.poll()
        elif outcome == "operator":
            sim.pump(int(delivered_ml))
            ctrl.poll()
            clock.advance(6)
            ctrl.stop()
        elif outcome == "column":
            sim.pump(int(delivered_ml))
            sim.local_stop()
            ctrl.poll()
        elif outcome == "alarm":
            sim.pump(int(delivered_ml))
            sim.fault()
            ctrl.poll()
        else:
            raise RuntimeError(outcome)
        clock.advance(20)
        sale_id = ctrl.reset()
        if sale_id is None:
            raise RuntimeError("учебная заправка не создала продажу")
        if payment != "unknown":
            clock.advance(4)
            ctrl.set_payment(sale_id, payment)
        return sale_id

    dispense(at(8, 12), "cash", "liters", ordered_ml=20_000, pump_s=80)
    dispense(at(8, 41), "card", "liters", ordered_ml=35_500, pump_s=110)
    dispense(at(9, 6), "cash", "amount", amount_tiyn=180_000, pump_s=80)
    dispense(at(9, 33), "cash", "full", target_ml=27_400, pump_s=100)
    dispense(
        at(10, 4),
        "cash",
        "liters",
        outcome="operator",
        ordered_ml=10_000,
        delivered_ml=6_200,
        pump_s=40,
    )
    dispense(at(10, 50), "card", "liters", ordered_ml=42_000, pump_s=130)

    clock.set(at(11, 10))
    ctrl.poll_with_bad_checksum_once()

    dispense(at(11, 22), "invoice", "full", target_ml=51_050, pump_s=150)
    dispense(at(12, 11), "cash", "liters", ordered_ml=18_750, pump_s=70)
    dispense(at(13, 4), "card", "amount", amount_tiyn=270_000, pump_s=100)
    dispense(
        at(14, 16),
        "cash",
        "liters",
        outcome="column",
        ordered_ml=40_000,
        delivered_ml=33_100,
        pump_s=90,
    )
    dispense(at(15, 6), "cash", "liters", ordered_ml=25_000, pump_s=90)
    dispense(
        at(16, 24),
        "unknown",
        "liters",
        outcome="alarm",
        ordered_ml=30_000,
        delivered_ml=11_200,
        pump_s=35,
    )
    clock.advance(25)
    ctrl.panel_note(
        "error",
        "02 — ошибка работы датчика импульсов турбинки. Кадр прислал только состояние 06 «авария».",
    )

    clock.set(at(17, 2))
    sold = store.dispensed_ml(int(ctrl.shift_id or 0))
    ctrl.panel_note("totalizer", f"{fmt_liters(ctrl.accounted_ml)} л, клавиши 9 0 2")
    clock.advance(20)
    ctrl.panel_note("shift_counter", f"{fmt_liters(sold)} л, клавиши 9 0 1, перед обнулением")
    clock.set(at(17, 5))
    ctrl.close_shift(ctrl.accounted_ml, sold)

    clock.set(at(17, 10))
    ctrl.open_shift(OPERATOR, PRICE_TIYN, ctrl.accounted_ml, number=15)
    store.set_meta("channel", "simulator")
    store.set_meta("sample", "1")
