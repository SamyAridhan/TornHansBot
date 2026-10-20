"""Store (single state file + migration) and settings."""

import json
import os
import tempfile
import unittest

from hans import settings as S
from hans.store import Store


class StoreTests(unittest.TestCase):
    def test_fresh_store_has_defaults(self):
        st = Store(os.path.join(tempfile.mkdtemp(), "s.json")).load()
        self.assertFalse(st.migrated)
        self.assertTrue(st.settings["depart"]["routes"]["chi"]["enabled"])
        self.assertFalse(st.settings["depart"]["routes"]["mex"]["enabled"])
        self.assertEqual(st.settings["digest"]["time"], "08:30")

    def test_roundtrip_atomic(self):
        p = os.path.join(tempfile.mkdtemp(), "s.json")
        st = Store(p).load()
        st["tg"]["offset"] = 9
        st.settings["alerts"]["happy_full"] = True
        st.save()
        self.assertFalse(os.path.exists(p + ".tmp"))
        again = Store(p).load()
        self.assertEqual(again["tg"]["offset"], 9)
        self.assertTrue(again.settings["alerts"]["happy_full"])

    def test_corrupt_file_recovers(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "s.json")
        with open(p, "w") as f:
            f.write("{nope")
        self.assertEqual(Store(p).load()["tg"], {})

    def test_migrates_every_legacy_file(self):
        d = tempfile.mkdtemp()
        legacy = {
            "state.json": {"energy": {"current": 5, "maximum": 150}},
            "schedule_state.json": {"digest_date": "2026-10-20", "chart_week": "2026-W43",
                                    "seeded": True},
            "commands_state.json": {"offset": 777},
            "pin_state.json": {"message_id": 55, "last_text": "x", "kbd_cleared": True},
            "travel_state.json": {"arrival": 123.0, "dest": "China", "alerted": True},
            "depart_watch.json": {"chi": {"enabled": False, "item": "Panda Plushie"},
                                  "mex": {"enabled": True, "item": ""}},
            "flights.json": {"chi": 1500, "mex": 1560},
            "cycles.json": {"chi": {"last": 5, "cycle": 2820}},
            "oc_state.json": {"ready": 999, "source": "manual", "fired": [86400]},
        }
        for name, data in legacy.items():
            with open(os.path.join(d, name), "w") as f:
                json.dump(data, f)
        st = Store(os.path.join(d, "hans_state.json")).load()
        self.assertTrue(st.migrated)
        self.assertEqual(st["bars"]["prev"]["energy"]["current"], 5)
        self.assertEqual(st["schedule"], {"digest_date": "2026-10-20", "chart_week": "2026-W43"})
        self.assertEqual(st["tg"]["offset"], 777)
        self.assertEqual(st["tg"]["pin_id"], 55)
        self.assertEqual(st["landing"]["dest"], "China")
        self.assertEqual(st["travel"]["flights"], {"chi": 1500, "mex": 1560})
        self.assertEqual(st["travel"]["cycles"]["chi"]["cycle"], 2820)
        self.assertEqual(st["oc"]["source"], "manual")
        routes = st.settings["depart"]["routes"]
        self.assertFalse(routes["chi"]["enabled"])           # your choice kept
        self.assertTrue(routes["mex"]["enabled"])
        self.assertEqual(routes["mex"]["item"], "Jaguar Plushie")  # blank -> default
        self.assertEqual(routes["uae"]["item"], "Camel Plushie")   # others defaulted

    def test_existing_state_wins_over_legacy(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, "commands_state.json"), "w") as f:
            json.dump({"offset": 1}, f)
        with open(os.path.join(d, "hans_state.json"), "w") as f:
            json.dump({"tg": {"offset": 50}}, f)
        st = Store(os.path.join(d, "hans_state.json")).load()
        self.assertFalse(st.migrated)
        self.assertEqual(st["tg"]["offset"], 50)


class SettingsTests(unittest.TestCase):
    def test_toggle_and_cycle(self):
        s = S.with_defaults({})
        S.apply(s, "hf")
        self.assertTrue(s["alerts"]["happy_full"])
        S.apply(s, "hf")
        self.assertFalse(s["alerts"]["happy_full"])
        S.apply(s, "em")                         # 25 -> 50
        self.assertEqual(s["alerts"]["energy_mark"], 50)
        for _ in range(4):
            S.apply(s, "em")                     # wraps around
        self.assertEqual(s["alerts"]["energy_mark"], 25)
        self.assertFalse(S.apply(s, "zz"))

    def test_cycle_from_unknown_value_starts_at_first(self):
        s = S.with_defaults({"landing": {"lead": 45}})
        S.apply(s, "ll")
        self.assertEqual(s["landing"]["lead"], 30)

    def test_new_defaults_appear_in_old_saves(self):
        s = S.with_defaults({"alerts": {"energy_full": False}})
        self.assertFalse(s["alerts"]["energy_full"])
        self.assertTrue(s["alerts"]["nerve_full"])

    def test_alert_config(self):
        s = S.with_defaults({"alerts": {"hospital": False, "energy_mark": 0},
                             "quiet": {"window": "23-07"}, "digest": {"time": "07:30"}})
        cfg = S.alert_config(s)
        self.assertFalse(cfg["alerts"]["hospital_in"])
        self.assertFalse(cfg["alerts"]["hospital_out"])
        self.assertIsNone(cfg["marks"]["energy"])
        self.assertEqual((cfg["quiet_hours"]["start"], cfg["quiet_hours"]["end"]), (23, 7))
        self.assertEqual((cfg["schedule"]["digest"]["hour"],
                          cfg["schedule"]["digest"]["minute"]), (7, 30))

    def test_show_value(self):
        self.assertEqual(S.show_value("hf", True), "✅")
        self.assertEqual(S.show_value("em", 0), "off")
        self.assertEqual(S.show_value("ll", 60), "60s")
        self.assertEqual(S.show_value("qw", "01-08"), "01:00–08:00")

    def test_every_page_key_is_in_spec(self):
        for _title, keys in S.PAGES.values():
            for k in keys:
                self.assertIn(k, S.SPEC)
                self.assertIsNotNone(S.get(S.DEFAULTS, S.SPEC[k][0]))


if __name__ == "__main__":
    unittest.main()
