#!/usr/bin/env python3
"""Tests for travel.py (stock parsing + depart math). Run: python3 -m unittest test_travel"""

import unittest
import travel


SAMPLE = {"stocks": {
    "chi": {"update": 1000, "stocks": [
        {"id": 274, "name": "Panda Plushie", "quantity": 883, "cost": 400,
         "nextRestock": 2000},
        {"id": 999, "name": "Peony", "quantity": 50, "cost": 5000, "nextRestock": None},
    ]},
    "MEX": {"update": 1000, "stocks": [
        {"id": 258, "name": "Jaguar Plushie", "quantity": 0, "cost": 10000,
         "nextRestock": "1970-01-01T00:33:20+00:00"},  # == epoch 2000
    ]},
}}


class CodeTests(unittest.TestCase):
    def test_resolve(self):
        self.assertEqual(travel.code_for("china"), "chi")
        self.assertEqual(travel.code_for("China"), "chi")
        self.assertEqual(travel.code_for("mex"), "mex")
        self.assertEqual(travel.code_for("uae"), "uae")
        self.assertIsNone(travel.code_for("atlantis"))

    def test_name(self):
        self.assertEqual(travel.country_name("chi"), "China")


class RestockEpochTests(unittest.TestCase):
    def test_int(self):
        self.assertEqual(travel._restock_epoch(2000), 2000)

    def test_millis(self):
        self.assertEqual(travel._restock_epoch(2000000), 2000000)  # seconds, big-but-ok
        self.assertEqual(travel._restock_epoch(2_000_000_000_000), 2_000_000_000)

    def test_iso(self):
        self.assertEqual(travel._restock_epoch("1970-01-01T00:33:20+00:00"), 2000)

    def test_none(self):
        self.assertIsNone(travel._restock_epoch(None))
        self.assertIsNone(travel._restock_epoch(""))
        self.assertIsNone(travel._restock_epoch("garbage"))


class ParseTests(unittest.TestCase):
    def test_parse_shape(self):
        p = travel.parse_stock(SAMPLE)
        self.assertIn("chi", p)
        self.assertIn("mex", p)  # lowercased
        panda = p["chi"]["items"][0]
        self.assertEqual(panda["qty"], 883)
        self.assertEqual(panda["cost"], 400)
        self.assertEqual(panda["restock"], 2000)
        self.assertEqual(p["mex"]["items"][0]["restock"], 2000)  # ISO parsed

    def test_find_item(self):
        p = travel.parse_stock(SAMPLE)
        self.assertEqual(travel.find_item(p, "chi", "panda")["id"], 274)
        self.assertEqual(travel.find_item(p, "chi", "274")["name"], "Panda Plushie")
        # no needle -> highest cost item
        self.assertEqual(travel.find_item(p, "chi", "")["name"], "Peony")
        self.assertIsNone(travel.find_item(p, "chi", "nothere"))


class DepartMathTests(unittest.TestCase):
    def test_depart_epoch(self):
        # land 120s after a restock at t=10000, flight 3600s -> depart 6520
        self.assertEqual(travel.depart_epoch(10000, 3600, 120), 6520)

    def test_next_catchable_future(self):
        self.assertEqual(
            travel.next_catchable_depart(10000, 3600, now=6000, land_after_seconds=120),
            6520)

    def test_next_catchable_unreachable_no_cycle(self):
        # depart already past, no cycle known -> None
        self.assertIsNone(
            travel.next_catchable_depart(10000, 3600, now=9000, land_after_seconds=120))

    def test_next_catchable_rolls_cycle(self):
        # depart 6520 is past at now=7000; cycle 2820s -> next depart 6520+2820=9340
        self.assertEqual(
            travel.next_catchable_depart(10000, 3600, now=7000, cycle_seconds=2820,
                                         land_after_seconds=120),
            9340)


MODEL = {"updated": 1000, "items": {
    "chi": {"274": {"interval": 2820, "last": 100000, "sellRate": 31.4}},
    "mex": {"258": {"interval": 2760, "last": 90000}},
}}


class RestockModelTests(unittest.TestCase):
    def test_interval_last(self):
        self.assertEqual(travel.model_interval_last(MODEL, "chi", 274), (2820, 100000))
        self.assertEqual(travel.model_interval_last(MODEL, "chi", "274"), (2820, 100000))
        self.assertEqual(travel.model_interval_last(MODEL, "chi", 999), (None, None))
        self.assertEqual(travel.model_interval_last(MODEL, "zzz", 1), (None, None))

    def test_next_restock_future(self):
        # last in the future -> itself
        self.assertEqual(travel.next_restock(100000, 2820, now=99000), 100000)

    def test_next_restock_rolls(self):
        # last=100000, interval=2820; now=104000 -> 100000+2*2820=105640
        self.assertEqual(travel.next_restock(100000, 2820, now=104000), 105640)

    def test_next_restock_guards(self):
        self.assertIsNone(travel.next_restock(0, 2820, now=1))
        self.assertIsNone(travel.next_restock(100000, 0, now=1))


class FetchTests(unittest.TestCase):
    def test_fetch_uses_fallback(self):
        calls = []
        def getter(url):
            calls.append(url)
            if url == travel.PROMBOT_URL:
                raise RuntimeError("prombot down")
            return SAMPLE
        p = travel.fetch_stock(getter)
        self.assertEqual(calls, [travel.PROMBOT_URL, travel.YATA_URL])
        self.assertIn("chi", p)

    def test_fetch_all_down(self):
        def getter(url):
            raise RuntimeError("down")
        self.assertEqual(travel.fetch_stock(getter), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
