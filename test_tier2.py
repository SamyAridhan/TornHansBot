#!/usr/bin/env python3
"""Tests for Tier 2 modules. Run: python3 -m unittest -v test_tier2"""

import csv
import os
import tempfile
import unittest

import tier2


class MarketParseTests(unittest.TestCase):
    def test_listings_dict_shape(self):
        raw = {"itemmarket": {"listings": [{"price": 900000}, {"price": 750000}]}}
        self.assertEqual(tier2.parse_market(raw), 750000)

    def test_listings_list_shape_cost_key(self):
        raw = {"itemmarket": [{"cost": 1200000}, {"cost": 1100000}]}
        self.assertEqual(tier2.parse_market(raw), 1100000)

    def test_empty_or_missing(self):
        self.assertIsNone(tier2.parse_market({}))
        self.assertIsNone(tier2.parse_market({"itemmarket": {"listings": []}}))

    def test_v2_shape_with_amount(self):
        raw = {"itemmarket": {"item": {"id": 206, "name": "Xanax"},
                              "listings": [{"price": 830000, "amount": 3},
                                           {"price": 815000, "amount": 1}]}}
        self.assertEqual(tier2.parse_market(raw), 815000)

    def test_v2_listings_top_level(self):
        raw = {"listings": [{"price": 500, "amount": 2}]}
        self.assertEqual(tier2.parse_market(raw), 500)


class MarketItemTests(unittest.TestCase):
    def test_name_and_image(self):
        raw = {"itemmarket": {"item": {"id": 206, "name": "Xanax",
                                       "image": "https://x/206.png"}, "listings": []}}
        meta = tier2.parse_market_item(raw)
        self.assertEqual(meta["name"], "Xanax")
        self.assertEqual(meta["image"], "https://x/206.png")

    def test_missing_item_defaults_none(self):
        meta = tier2.parse_market_item({})
        self.assertIsNone(meta["name"])
        self.assertIsNone(meta["image"])

    def test_non_url_image_dropped(self):
        raw = {"itemmarket": {"item": {"name": "Xanax", "image": "items/206.png"}}}
        meta = tier2.parse_market_item(raw)
        self.assertEqual(meta["name"], "Xanax")
        self.assertIsNone(meta["image"])   # not a full URL -> fall back later

    def test_ignores_bad_entries(self):
        raw = {"itemmarket": {"listings": [{"price": None}, {"nope": 1}, {"price": 500}]}}
        self.assertEqual(tier2.parse_market(raw), 500)


class MarketEvaluateTests(unittest.TestCase):
    def setUp(self):
        self.watch = {"365": {"name": "Metal Detector", "target": 1000000}}

    def test_deal_fires_when_crosses_below(self):
        prev = {"365": 1200000}
        cur = {"365": 950000}
        events, _ = tier2.evaluate_market(prev, cur, self.watch)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["type"], "market_deal")
        self.assertEqual(events[0]["price"], 950000)

    def test_no_repeat_while_still_cheap(self):
        prev = {"365": 950000}   # already a deal last poll
        cur = {"365": 900000}    # still a deal, even cheaper
        events, _ = tier2.evaluate_market(prev, cur, self.watch)
        self.assertEqual(events, [])

    def test_no_alert_above_target(self):
        prev = {"365": 1300000}
        cur = {"365": 1200000}
        events, _ = tier2.evaluate_market(prev, cur, self.watch)
        self.assertEqual(events, [])

    def test_first_time_seen_cheap_fires(self):
        prev = {}                # never seen before
        cur = {"365": 800000}
        events, _ = tier2.evaluate_market(prev, cur, self.watch)
        self.assertEqual(len(events), 1)

    def test_refire_after_going_back_up(self):
        w = self.watch
        # deal -> back above -> deal again should alert on the second deal
        e1, s1 = tier2.evaluate_market({"365": 1200000}, {"365": 900000}, w)  # fires
        e2, s2 = tier2.evaluate_market(s1, {"365": 1100000}, w)               # back up, silent
        e3, _ = tier2.evaluate_market(s2, {"365": 950000}, w)                 # deal again, fires
        self.assertEqual(len(e1), 1)
        self.assertEqual(e2, [])
        self.assertEqual(len(e3), 1)


class OcEvaluateTests(unittest.TestCase):
    def test_open_high_cpr_slot_fires(self):
        slots = [{"scenario": "Mob Mentality", "role": "Driver", "cpr": 80, "open": True}]
        events, state = tier2.evaluate_oc([], slots, min_cpr=50)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["role"], "Driver")
        self.assertIn("Mob Mentality|Driver", state)

    def test_low_cpr_slot_no_alert(self):
        slots = [{"scenario": "Mob Mentality", "role": "Muscle", "cpr": 20, "open": True}]
        events, _ = tier2.evaluate_oc([], slots, min_cpr=50)
        self.assertEqual(events, [])

    def test_closed_slot_no_alert(self):
        slots = [{"scenario": "X", "role": "Y", "cpr": 90, "open": False}]
        events, state = tier2.evaluate_oc([], slots, min_cpr=50)
        self.assertEqual(events, [])
        self.assertEqual(state, [])

    def test_no_repeat_for_same_open_slot(self):
        slots = [{"scenario": "X", "role": "Y", "cpr": 90, "open": True}]
        e1, s1 = tier2.evaluate_oc([], slots, min_cpr=50)         # fires
        e2, _ = tier2.evaluate_oc(s1, slots, min_cpr=50)          # same slot still open, silent
        self.assertEqual(len(e1), 1)
        self.assertEqual(e2, [])

    def test_new_slot_after_previous_fires(self):
        first = [{"scenario": "X", "role": "Y", "cpr": 90, "open": True}]
        _, s1 = tier2.evaluate_oc([], first, min_cpr=50)
        second = first + [{"scenario": "X", "role": "Z", "cpr": 70, "open": True}]
        e2, _ = tier2.evaluate_oc(s1, second, min_cpr=50)
        self.assertEqual(len(e2), 1)
        self.assertEqual(e2[0]["role"], "Z")


class HistoryTests(unittest.TestCase):
    def test_parse_battlestats(self):
        raw = {"strength": 100, "defense": 200, "speed": 150, "dexterity": 50, "total": 500}
        s = tier2.parse_battlestats(raw)
        self.assertEqual(s["total"], 500)
        self.assertEqual(s["defense"], 200)

    def test_parse_battlestats_defaults(self):
        s = tier2.parse_battlestats({})
        self.assertEqual(s, {"strength": 0, "defense": 0, "speed": 0, "dexterity": 0, "total": 0})

    def test_append_writes_header_then_rows(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "history.csv")
        stats = {"strength": 10, "defense": 20, "speed": 30, "dexterity": 40, "total": 100}
        tier2.append_history(path, "2026-09-29T10:00", stats, nnb=25)
        tier2.append_history(path, "2026-09-29T11:00",
                             {**stats, "total": 110, "strength": 20}, nnb=25)
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], tier2.HISTORY_HEADER)      # header once
        self.assertEqual(len(rows), 3)                        # header + 2 data rows
        self.assertEqual(rows[1][0], "2026-09-29T10:00")
        self.assertEqual(rows[2][5], "110")                   # total column updated


class FormatTests(unittest.TestCase):
    def test_market_and_oc_format(self):
        m = tier2.format_tier2_event(
            {"type": "market_deal", "name": "Metal Detector", "price": 900000, "target": 1000000})
        self.assertIn("Metal Detector", m)
        self.assertIn("900,000", m)
        o = tier2.format_tier2_event(
            {"type": "oc_slot", "scenario": "Mob Mentality", "role": "Driver", "cpr": 80})
        self.assertIn("Driver", o)
        self.assertIn("80%", o)


if __name__ == "__main__":
    unittest.main(verbosity=2)
