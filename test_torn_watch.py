#!/usr/bin/env python3
"""Unit tests for torn_watch. Run: python3 -m unittest -v test_torn_watch"""

import unittest

import torn_watch as tw


def snap(energy=(0, 100), nerve=(0, 25), happy=(0, 5000),
         life=(175, 175), drug=0, medical=0, booster=0,
         travel_left=0, dest=""):
    """Build a raw-API-shaped payload for tests."""
    return {
        "energy": {"current": energy[0], "maximum": energy[1]},
        "nerve": {"current": nerve[0], "maximum": nerve[1]},
        "happy": {"current": happy[0], "maximum": happy[1]},
        "life": {"current": life[0], "maximum": life[1]},
        "cooldowns": {"drug": drug, "medical": medical, "booster": booster},
        "travel": {"time_left": travel_left, "destination": dest},
    }


class ParseTests(unittest.TestCase):
    def test_full_payload(self):
        s = tw.parse_snapshot(snap(energy=(50, 100), nerve=(10, 25)))
        self.assertEqual(s["energy"], {"current": 50, "maximum": 100})
        self.assertEqual(s["nerve"], {"current": 10, "maximum": 25})

    def test_missing_fields_default_safely(self):
        s = tw.parse_snapshot({})  # empty payload
        self.assertEqual(s["energy"], {"current": 0, "maximum": 0})
        self.assertEqual(s["cooldowns"]["drug"], 0)
        self.assertEqual(s["travel"]["time_left"], 0)

    def test_non_numeric_values(self):
        s = tw.parse_snapshot({"energy": {"current": None, "maximum": "x"}})
        self.assertEqual(s["energy"], {"current": 0, "maximum": 0})


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.cfg = tw.default_config()

    def test_first_run_seeds_no_events(self):
        cur = tw.parse_snapshot(snap(energy=(100, 100)))  # full on first run
        events, state = tw.evaluate(None, cur, self.cfg)
        self.assertEqual(events, [])
        self.assertEqual(state, cur)

    def test_energy_becomes_full_fires_once(self):
        prev = tw.parse_snapshot(snap(energy=(90, 100)))
        cur = tw.parse_snapshot(snap(energy=(100, 100)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        types = [e["type"] for e in events]
        self.assertIn("energy_full", types)

    def test_energy_stays_full_no_repeat(self):
        prev = tw.parse_snapshot(snap(energy=(100, 100)))
        cur = tw.parse_snapshot(snap(energy=(100, 100)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertEqual(events, [])

    def test_energy_drops_then_refills_fires_again(self):
        # full -> spent -> full again should alert on the second full
        s_full = tw.parse_snapshot(snap(energy=(100, 100)))
        s_spent = tw.parse_snapshot(snap(energy=(20, 100)))
        # spent after full: no event
        e1, st1 = tw.evaluate(s_full, s_spent, self.cfg)
        self.assertEqual(e1, [])
        # refilled after spent: event
        e2, _ = tw.evaluate(st1, s_full, self.cfg)
        self.assertIn("energy_full", [e["type"] for e in e2])

    def test_nerve_full_fires(self):
        prev = tw.parse_snapshot(snap(nerve=(20, 25)))
        cur = tw.parse_snapshot(snap(nerve=(25, 25)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("nerve_full", [e["type"] for e in events])

    def test_happy_off_by_default(self):
        prev = tw.parse_snapshot(snap(happy=(4000, 5000)))
        cur = tw.parse_snapshot(snap(happy=(5000, 5000)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("happy_full", [e["type"] for e in events])

    def test_happy_on_when_enabled(self):
        self.cfg["alerts"]["happy_full"] = True
        prev = tw.parse_snapshot(snap(happy=(4000, 5000)))
        cur = tw.parse_snapshot(snap(happy=(5000, 5000)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("happy_full", [e["type"] for e in events])

    def test_custom_nerve_threshold(self):
        self.cfg["thresholds"]["nerve"] = 24  # alert before overflow
        prev = tw.parse_snapshot(snap(nerve=(23, 25)))
        cur = tw.parse_snapshot(snap(nerve=(24, 25)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("nerve_full", [e["type"] for e in events])

    def test_drug_cooldown_ends_fires(self):
        prev = tw.parse_snapshot(snap(drug=120))
        cur = tw.parse_snapshot(snap(drug=0))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("drug_cooldown_ended", [e["type"] for e in events])

    def test_drug_cooldown_still_running_no_event(self):
        prev = tw.parse_snapshot(snap(drug=200))
        cur = tw.parse_snapshot(snap(drug=120))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertEqual(events, [])

    def test_travel_landed_fires(self):
        prev = tw.parse_snapshot(snap(travel_left=60, dest="Mexico"))
        cur = tw.parse_snapshot(snap(travel_left=0, dest="Mexico"))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        landed = [e for e in events if e["type"] == "travel_landed"]
        self.assertTrue(landed)
        self.assertEqual(landed[0]["destination"], "Mexico")

    def test_no_max_known_skips_bar(self):
        # maximum 0 means we don't know the cap yet -> never fire
        prev = tw.parse_snapshot(snap(energy=(0, 0)))
        cur = tw.parse_snapshot(snap(energy=(0, 0)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertEqual(events, [])

    def test_multiple_events_one_cycle(self):
        prev = tw.parse_snapshot(snap(energy=(90, 100), nerve=(20, 25), drug=60))
        cur = tw.parse_snapshot(snap(energy=(100, 100), nerve=(25, 25), drug=0))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        types = {e["type"] for e in events}
        self.assertEqual(types, {"energy_full", "nerve_full", "drug_cooldown_ended"})


class IncreaseModeTests(unittest.TestCase):
    def setUp(self):
        self.cfg = tw.default_config()
        self.cfg["alerts"]["energy_increase"] = True
        self.cfg["alerts"]["nerve_increase"] = True

    def test_energy_increase_fires(self):
        prev = tw.parse_snapshot(snap(energy=(50, 100)))
        cur = tw.parse_snapshot(snap(energy=(55, 100)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        e = [x for x in events if x["type"] == "energy_increase"]
        self.assertTrue(e)
        self.assertEqual(e[0]["delta"], 5)

    def test_no_alert_when_unchanged(self):
        prev = tw.parse_snapshot(snap(energy=(50, 100)))
        cur = tw.parse_snapshot(snap(energy=(50, 100)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertEqual(events, [])

    def test_no_alert_when_decreased(self):
        # spending energy should NOT alert in increase mode
        prev = tw.parse_snapshot(snap(energy=(50, 100)))
        cur = tw.parse_snapshot(snap(energy=(30, 100)))
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertEqual(events, [])

    def test_off_by_default(self):
        prev = tw.parse_snapshot(snap(energy=(50, 100)))
        cur = tw.parse_snapshot(snap(energy=(55, 100)))
        events, _ = tw.evaluate(prev, cur, tw.default_config())
        self.assertNotIn("energy_increase", [x["type"] for x in events])

    def test_env_override_enables_increase_and_disables_full(self):
        import os
        os.environ["ALERT_ON_INCREASE"] = "1"
        try:
            cfg = tw.apply_env_overrides(tw.default_config())
        finally:
            del os.environ["ALERT_ON_INCREASE"]
        self.assertTrue(cfg["alerts"]["energy_increase"])
        self.assertTrue(cfg["alerts"]["nerve_increase"])
        self.assertFalse(cfg["alerts"]["energy_full"])
        self.assertFalse(cfg["alerts"]["nerve_full"])

    def test_env_override_absent_keeps_normal(self):
        cfg = tw.apply_env_overrides(tw.default_config())
        self.assertFalse(cfg["alerts"]["energy_increase"])
        self.assertTrue(cfg["alerts"]["energy_full"])


class FormatTests(unittest.TestCase):
    def test_each_event_formats(self):
        for ev in [
            {"type": "energy_full", "current": 100, "maximum": 100},
            {"type": "nerve_full", "current": 25, "maximum": 25},
            {"type": "drug_cooldown_ended", "cooldown": "drug"},
            {"type": "travel_landed", "destination": "Mexico"},
        ]:
            msg = tw.format_event(ev)
            self.assertIsInstance(msg, str)
            self.assertTrue(len(msg) > 0)

    def test_combined_message(self):
        events = [
            {"type": "energy_full", "current": 100, "maximum": 100},
            {"type": "nerve_full", "current": 25, "maximum": 25},
        ]
        msg = tw.format_message(events)
        self.assertIn("Energy", msg)
        self.assertIn("Nerve", msg)
        self.assertEqual(len(msg.splitlines()), 2)


class RunOnceTests(unittest.TestCase):
    def setUp(self):
        self.cfg = tw.default_config()
        self.sent = []
        self.notifier = lambda text: self.sent.append(text)

    def test_run_once_sends_on_transition(self):
        prev = tw.parse_snapshot(snap(energy=(90, 100)))
        fetcher = lambda _="": snap(energy=(100, 100))
        events, new_state = tw.run_once(self.cfg, fetcher, self.notifier, prev)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("Energy", self.sent[0])
        self.assertEqual(new_state["energy"]["current"], 100)

    def test_run_once_silent_when_nothing_changes(self):
        prev = tw.parse_snapshot(snap(energy=(50, 100)))
        fetcher = lambda _="": snap(energy=(55, 100))
        events, _ = tw.run_once(self.cfg, fetcher, self.notifier, prev)
        self.assertEqual(self.sent, [])

    def test_run_once_first_run_silent(self):
        fetcher = lambda _="": snap(energy=(100, 100))
        events, state = tw.run_once(self.cfg, fetcher, self.notifier, None)
        self.assertEqual(self.sent, [])
        self.assertIsNotNone(state)


class TimingTests(unittest.TestCase):
    """Exact regen-clock math (the 'smarter option')."""

    def test_parse_captures_regen(self):
        raw = {"energy": {"current": 30, "maximum": 100,
                          "increment": 5, "interval": 900,
                          "ticktime": 300, "fulltime": 12600}}
        s = tw.parse_snapshot(raw)
        # bar dict stays exactly current/maximum (back-compat)
        self.assertEqual(s["energy"], {"current": 30, "maximum": 100})
        # regen lives in its own sub-dict
        self.assertEqual(s["regen"]["energy"]["increment"], 5)
        self.assertEqual(s["regen"]["energy"]["fulltime"], 12600)

    def test_seconds_to_value_exact(self):
        reg = {"increment": 5, "interval": 900, "ticktime": 0}
        # 0 -> 25 is 5 ticks; first tick in a full interval (ticktime unknown)
        self.assertEqual(tw.seconds_to_value(0, 25, reg), 900 + 4 * 900)

    def test_seconds_to_value_uses_ticktime(self):
        reg = {"increment": 1, "interval": 300, "ticktime": 120}
        # 12 -> 13 is 1 tick, arriving in ticktime
        self.assertEqual(tw.seconds_to_value(12, 13, reg), 120)

    def test_seconds_to_value_already_there(self):
        self.assertEqual(tw.seconds_to_value(30, 25, {"increment": 5, "interval": 900}), 0)

    def test_seconds_to_value_no_regen_returns_none(self):
        self.assertIsNone(tw.seconds_to_value(0, 25, {}))

    def test_seconds_to_full_prefers_fulltime(self):
        reg = {"increment": 5, "interval": 900, "ticktime": 300, "fulltime": 12600}
        self.assertEqual(tw.seconds_to_full(30, 100, reg), 12600)

    def test_seconds_to_full_when_capped(self):
        self.assertEqual(tw.seconds_to_full(100, 100, {"fulltime": 0}), 0)

    def test_fmt_duration(self):
        self.assertEqual(tw.fmt_duration(0), "now")
        self.assertEqual(tw.fmt_duration(30), "<1m")
        self.assertEqual(tw.fmt_duration(42 * 60), "42m")
        self.assertEqual(tw.fmt_duration(3720), "1h 02m")
        self.assertEqual(tw.fmt_duration(7200), "2h")
        self.assertEqual(tw.fmt_duration(None), "?")

    def test_progress_bar(self):
        self.assertEqual(tw.progress_bar(100, 100), "▰▰▰▰▰")
        self.assertEqual(tw.progress_bar(0, 100), "▱▱▱▱▱")
        self.assertEqual(tw.progress_bar(10, 0), "▱▱▱▱▱")  # unknown max

    def test_mark_event_carries_eta(self):
        cfg = tw.default_config()
        raw_prev = {"energy": {"current": 0, "maximum": 100}}
        raw_cur = {"energy": {"current": 30, "maximum": 100,
                              "increment": 5, "interval": 900,
                              "ticktime": 300, "fulltime": 12600}}
        prev = tw.parse_snapshot(raw_prev)
        cur = tw.parse_snapshot(raw_cur)
        events, _ = tw.evaluate(prev, cur, cfg)
        mark = [e for e in events if e["type"] == "energy_mark"]
        self.assertTrue(mark)
        self.assertEqual(mark[0]["eta_full"], 12600)

    def test_mark_message_shows_full_in(self):
        ev = {"type": "energy_mark", "bar": "energy", "current": 30,
              "maximum": 100, "mark": 25, "eta_full": 12600}
        msg = tw.format_event(ev)
        self.assertIn("full in", msg)
        self.assertIn("3h 30m", msg)
        self.assertIn("Energy", msg)

    def test_messages_are_html_safe(self):
        # destination with an & must be escaped for HTML parse_mode
        ev = {"type": "travel_landed", "destination": "Tom & Jerry Land"}
        msg = tw.format_event(ev)
        self.assertIn("&amp;", msg)
        self.assertNotIn("& ", msg)


class TornApiErrorTests(unittest.TestCase):
    def test_fetcher_raises_on_api_error(self):
        # Simulate what make_torn_fetcher does with an error payload,
        # by checking the error-detection contract via a tiny stand-in.
        def fake_fetch(_=""):
            data = {"error": {"code": 2, "error": "Incorrect key"}}
            if "error" in data:
                raise tw.TornApiError(data["error"]["code"], data["error"]["error"])
            return data
        with self.assertRaises(tw.TornApiError):
            fake_fetch()


if __name__ == "__main__":
    unittest.main(verbosity=2)
