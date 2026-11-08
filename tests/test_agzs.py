import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

from agzs.capabilities import FACTS, render_markdown
from agzs.controller import Controller, ManualClock, OperatorError
from agzs.dispenser import Dispenser
from agzs.export import events_text, sales_csv, sales_json
from agzs.format import ZONE, amount_tiyn, parse_liters, parse_money
from agzs.link import InProcessLink, SerialLink, _write_all
from agzs.protocol import FRAME_LEN, Frame, ProtocolError, decode_frame, encode_frame
from agzs.seed import OPEN_TOTALIZER_ML, PRICE_TIYN, seed_demo
from agzs.server import open_app, serve
from agzs.store import Store


def world(folder: Path):
    store = Store(folder / "t.sqlite")
    clock = ManualClock(datetime(2026, 11, 8, 8, 0, tzinfo=ZONE))
    dispenser = Dispenser(number=1)
    ctrl = Controller(
        InProcessLink(dispenser),
        store,
        clock=clock,
        column_no=1,
        simulator=dispenser,
    )
    return ctrl, store, clock, dispenser


class ProtocolTest(unittest.TestCase):
    def test_roundtrip_and_checksum(self) -> None:
        frame = Frame(1, "4", 9000, 20000, "00", "05")
        raw = encode_frame(frame)
        self.assertEqual(len(raw), FRAME_LEN)
        self.assertEqual(raw[0], 0x01)
        self.assertEqual(decode_frame(raw), frame)
        broken = bytearray(raw)
        broken[-1] ^= 0xFF
        with self.assertRaises(ProtocolError):
            decode_frame(bytes(broken))

    def test_general_stop_has_no_reply(self) -> None:
        dispenser = Dispenser(number=1, price_tiyn=100)
        dispenser.code = "03"
        dispenser.mode = "dose"
        reply = dispenser.handle(Frame(0, "7", 0, 0, "00", "00"))
        self.assertIsNone(reply)
        self.assertEqual(dispenser.code, "04")


class DispenserTest(unittest.TestCase):
    def test_dose_completes_on_remaining_zero(self) -> None:
        dispenser = Dispenser(price_tiyn=0)
        ready = dispenser.handle(Frame(1, "1", 9000, 20000, "00", "00"))
        assert ready is not None
        self.assertEqual(ready.code, "01")
        self.assertEqual(ready.volume_ml, 20000)
        running = dispenser.handle(Frame(1, "5", 9000, 0, "00", "00"))
        assert running is not None
        self.assertEqual(dispenser.pump(20000), 20000)
        stopped = dispenser.handle(Frame(1, "4", 0, 0, "00", "00"))
        assert stopped is not None
        self.assertEqual(stopped.code, "04")
        self.assertEqual(stopped.volume_ml, 0)

    def test_full_tank_reports_dispensed_volume(self) -> None:
        dispenser = Dispenser()
        dispenser.handle(Frame(1, "9", 9000, 0, "00", "00"))
        dispenser.set_full_target(27400)
        dispenser.handle(Frame(1, "5", 9000, 0, "00", "00"))
        self.assertEqual(dispenser.pump(999000), 27400)
        reply = dispenser.handle(Frame(1, "4", 0, 0, "00", "00"))
        assert reply is not None
        self.assertEqual(reply.code, "07")
        self.assertEqual(reply.volume_ml, 27400)

    def test_command_rejected_in_wrong_state(self) -> None:
        dispenser = Dispenser(price_tiyn=9000)
        reply = dispenser.handle(Frame(1, "5", 9000, 0, "00", "00"))
        assert reply is not None
        self.assertEqual(reply.error, "02")
        self.assertEqual(reply.code, "05")


class SampleShiftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "t.sqlite")
        seed_demo(self.store)
        self.shift = self.store.shift_by_number(14)
        assert self.shift is not None

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_totals_and_reasons(self) -> None:
        sales = self.store.sales(int(self.shift["id"]))
        self.assertEqual(len(sales), 12)
        self.assertEqual(sum(item["dispensed_ml"] for item in sales), 320_200)
        self.assertEqual(sum(item["amount_tiyn"] for item in sales), 2_881_800)
        self.assertEqual(sales[0]["totalizer_before_ml"], OPEN_TOTALIZER_ML)
        for previous, current in zip(sales, sales[1:]):
            self.assertEqual(previous["totalizer_after_ml"], current["totalizer_before_ml"])
        self.assertEqual(sales[-1]["totalizer_after_ml"], OPEN_TOTALIZER_ML + 320_200)
        self.assertEqual(
            [item["stop_reason"] for item in sales].count("доза набрана"),
            7,
        )
        self.assertEqual([item["stop_reason"] for item in sales].count("бак заполнен"), 2)
        self.assertEqual([item["payment"] for item in sales].count("cash"), 7)
        self.assertEqual([item["payment"] for item in sales].count("unknown"), 1)
        self.assertEqual(sales[11]["stop_reason"], "авария")
        self.assertEqual(sales[4]["dispensed_ml"], 6_200)
        self.assertEqual(sales[4]["ordered_ml"], 10_000)

    def test_journal_links_every_dose_and_names_the_gap(self) -> None:
        events = self.store.events(int(self.shift["id"]))
        codes = [item["code"] for item in events]
        self.assertEqual(codes.count("SALE"), 12)
        self.assertEqual(codes.count("DOSE"), 12)
        self.assertEqual(codes.count("FLOW"), 12)
        self.assertEqual(codes.count("STOP"), 11)
        self.assertEqual(codes.count("ALARM"), 1)
        self.assertEqual(codes.count("E227"), 1)
        self.assertEqual(codes.count("RECONCILE"), 1)
        self.assertNotIn("STATE", codes)
        doses = [item for item in events if item["code"] == "DOSE"]
        self.assertTrue(all(item["sale_id"] for item in doses))
        text = " ".join(item["message"] for item in events)
        self.assertIn("код 06", text)
        self.assertIn("9 0 2", text)
        self.assertIn("Учебная смена", text)
        self.assertIn("Сверка сошлась", text)
        reconcile = next(item for item in events if item["code"] == "RECONCILE")
        self.assertEqual(reconcile["severity"], "info")

    def test_exports_use_comma_and_timezone(self) -> None:
        raw = sales_csv(self.store, self.shift).decode("utf-8-sig")
        self.assertIn("Отпущено, л", raw)
        self.assertIn("20,000", raw)
        self.assertIn("1008,00", raw)
        self.assertIn("+05", raw)
        self.assertNotIn("20.000", raw)
        self.assertTrue(raw.startswith("Номер;"))
        payload = sales_json(self.store, self.shift).decode("utf-8")
        self.assertIn("320,200", payload)
        self.assertIn("учётный", payload)
        journal = events_text(self.store, self.shift)
        self.assertIn("ЖУРНАЛ СОБЫТИЙ", journal)
        self.assertIn("Искра 1.72", journal)
        self.assertGreater(journal.count("\n"), 40)


class EdgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.ctrl, self.store, self.clock, self.dispenser = world(Path(self.tmp.name))
        self.ctrl.open_shift("оператор", PRICE_TIYN, 1_000_000, number=1)

    def tearDown(self) -> None:
        self.store.close()
        self.tmp.cleanup()

    def test_rejected_start_and_empty_reset(self) -> None:
        before = len(self.store.events(1))
        self.ctrl.poll()
        self.assertEqual(len(self.store.events(1)), before)
        with self.assertRaises(OperatorError):
            self.ctrl.start()
        self.assertTrue(any(item["code"] == "E226" for item in self.store.events(1)))
        self.ctrl.load_liters(5_000)
        self.assertIsNone(self.ctrl.reset())
        self.assertEqual(self.store.sales(1), [])

    def test_amount_rounds_to_millilitres(self) -> None:
        self.ctrl.load_amount(100)
        self.assertEqual(self.ctrl.ordered_ml, (100 * 1000) // PRICE_TIYN)
        note = " ".join(item["message"] for item in self.store.events(1))
        self.assertIn("не делится точно", note)
        self.ctrl.reset()

    def test_uncommanded_start_becomes_a_sale(self) -> None:
        self.dispenser.code = "03"
        self.dispenser.mode = "dose"
        self.dispenser.remaining_ml = 5_000
        self.dispenser.ordered_ml = 5_000
        self.ctrl.poll()
        self.dispenser.remaining_ml = 0
        self.dispenser.dispensed_ml = 5_000
        self.dispenser.totalizer_ml += 5_000
        self.dispenser.code = "04"
        self.ctrl.poll()
        sale_id = self.ctrl.reset()
        sale = self.store.sale(int(sale_id or 0))
        assert sale is not None
        self.assertEqual(sale["dispensed_ml"], 5_000)
        self.assertEqual(sale["uncommanded"], 1)
        self.assertEqual(sale["amount_tiyn"], amount_tiyn(5_000, PRICE_TIYN))

    def test_mismatch_on_close_is_a_warning(self) -> None:
        self.ctrl.perform_fill("литры", liters_ml=10_000, payment="card")
        self.ctrl.close_shift(self.ctrl.accounted_ml - 50, 0)
        events = self.store.events()
        reconcile = next(item for item in events if item["code"] == "RECONCILE")
        self.assertEqual(reconcile["severity"], "warning")
        self.assertIn("не сошлась", reconcile["message"])

    def test_payment_on_closed_shift_does_not_steal_the_open_one(self) -> None:
        sale_id = self.ctrl.perform_fill("литры", liters_ml=1_000, payment="unknown")
        self.ctrl.close_shift(self.ctrl.accounted_ml, 1_000)
        self.ctrl.open_shift("оператор", PRICE_TIYN, self.ctrl.accounted_ml, number=2)
        self.ctrl.set_payment(int(sale_id), "coupon")
        self.assertEqual(self.ctrl.shift_id, self.store.open_shift_row()["id"])
        sale = self.store.sale(int(sale_id))
        assert sale is not None
        self.assertEqual(sale["payment"], "coupon")


class SerialTest(unittest.TestCase):
    def test_frame_over_pty(self) -> None:
        master, slave = os.openpty()
        os.set_blocking(master, False)
        from agzs.link import _raw_9600

        _raw_9600(master)
        name = os.ttyname(slave)
        dispenser = Dispenser(price_tiyn=9000)
        stop = threading.Event()

        def answer() -> None:
            buf = bytearray()
            while not stop.is_set():
                try:
                    part = os.read(master, 64)
                except BlockingIOError:
                    time.sleep(0.01)
                    continue
                if not part:
                    continue
                buf += part
                while len(buf) >= FRAME_LEN:
                    frame = decode_frame(bytes(buf[:FRAME_LEN]))
                    del buf[:FRAME_LEN]
                    reply = dispenser.handle(frame)
                    if reply is not None:
                        _write_all(master, reply.encode(), 2)

        thread = threading.Thread(target=answer, daemon=True)
        thread.start()
        link = SerialLink(name, timeout=2)
        try:
            reply = link.exchange(Frame(1, "1", 9000, 15000, "00", "00"))
        finally:
            stop.set()
            link.close()
            os.close(master)
        assert reply is not None
        self.assertEqual(reply.code, "01")
        self.assertEqual(reply.volume_ml, 15000)
        self.assertEqual(reply.price_tiyn, 9000)


class HttpTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.app = open_app(Path(self.tmp.name) / "t.sqlite")
        self.server = serve(self.app, 0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.app.store.close()
        self.tmp.cleanup()

    def _get(self, path: str) -> bytes:
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}") as response:
            return response.read()

    def _post(self, path: str, body: dict) -> dict:
        import json
        import urllib.request

        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-AGZS": "1"},
        )
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read().decode("utf-8"))

    def test_journal_page_and_fill(self) -> None:
        page = self._get("/").decode("utf-8")
        self.assertIn("Журнал событий", page)
        self.assertIn("Что отдаёт УЗГС-01", page)
        csv_body = self._get("/api/export/sales.csv?number=14").decode("utf-8-sig")
        self.assertEqual(csv_body.count("\n"), 13)
        before = len(self.app.store.sales())
        self._post("/api/fill", {"mode": "liters", "value": "15,5", "payment": "cash"})
        sales = self.app.store.sales()
        self.assertEqual(len(sales), before + 1)
        self.assertEqual(sales[-1]["dispensed_ml"], 15_500)
        self.assertEqual(sales[-1]["payment"], "cash")
        denied = __import__("urllib.request", fromlist=["Request"]).Request(
            f"http://127.0.0.1:{self.port}/api/fill",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with self.assertRaises(Exception):
            __import__("urllib.request", fromlist=["urlopen"]).urlopen(denied)


class FormatTest(unittest.TestCase):
    def test_scales(self) -> None:
        self.assertEqual(parse_liters("20"), 20_000)
        self.assertEqual(parse_liters("35,50"), 35_500)
        self.assertEqual(parse_money("90"), 9_000)
        self.assertEqual(parse_money("1 800,00"), 180_000)
        self.assertEqual(amount_tiyn(320_200, 9_000), 2_881_800)
        with self.assertRaises(ValueError):
            parse_liters("1,2345")


class DocumentTest(unittest.TestCase):
    def test_manual_mentions_what_the_frame_cannot_carry(self) -> None:
        text = render_markdown()
        for needle in ("Искра 1.72", "9600", "АСКА-01", "9 0 2", "9 0 1", "код 06", "температур"):
            self.assertIn(needle, text)
        groups = {item.group for item in FACTS}
        self.assertEqual(groups, {"protocol", "panel", "missing", "connection"})
        path = Path(__file__).resolve().parents[1] / "docs" / "uzsg-01.md"
        self.assertEqual(path.read_text(encoding="utf-8"), text)


if __name__ == "__main__":
    unittest.main()
