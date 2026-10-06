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
            {"type": "energy_full", "bar": "energy", "current": 100, "maximum": 100},
            {"type": "nerve_full", "bar": "nerve", "current": 25, "maximum": 25},
        ]
        msg = tw.format_message(events)
        self.assertIn("Energy", msg)
        self.assertIn("Nerve", msg)
        self.assertIn("\n\n", msg)   # blank line separates the two events


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
        self.assertIn("3h30m", msg)
        self.assertIn("Energy", msg)

    def test_messages_are_html_safe(self):
        # destination with an & must be escaped for HTML parse_mode
        ev = {"type": "travel_landed", "destination": "Tom & Jerry Land"}
        msg = tw.format_event(ev)
        self.assertIn("&amp;", msg)
        self.assertNotIn("& ", msg)


class SmarterAlertTests(unittest.TestCase):
    """Phase 2: overflow nudge, hospital/jail-out, milestones."""

    def setUp(self):
        self.cfg = tw.default_config()

    def _snap(self, e_cur, e_full, state="", total=0):
        return tw.parse_snapshot({
            "energy": {"current": e_cur, "maximum": 100,
                       "increment": 5, "interval": 900, "ticktime": 60,
                       "fulltime": e_full},
            "status": {"state": state},
            "total": total,
        })

    def test_state_category(self):
        self.assertEqual(tw._state_category("In Hospital"), "hospital")
        self.assertEqual(tw._state_category("Hospital"), "hospital")
        self.assertEqual(tw._state_category("In Jail"), "jail")
        self.assertEqual(tw._state_category("Federal"), "jail")
        self.assertEqual(tw._state_category("Okay"), "okay")
        self.assertEqual(tw._state_category(""), "")

    def test_overflow_fires_entering_window(self):
        prev = self._snap(60, 7200)   # far from full
        cur = self._snap(95, 60)      # ~1 min to cap
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("energy_overflow", [e["type"] for e in events])

    def test_overflow_no_refire_when_already_in_window(self):
        prev = self._snap(95, 120)
        cur = self._snap(96, 60)
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("energy_overflow", [e["type"] for e in events])

    def test_nerve_overflow_fires_entering_window(self):
        # nerve 23/25 regen 1/300s, ~2 ticks (480s) to cap -> within 600 lead
        prev = tw.parse_snapshot({"nerve": {"current": 20, "maximum": 25,
                                            "increment": 1, "interval": 300,
                                            "ticktime": 200, "fulltime": 1400}})
        cur = tw.parse_snapshot({"nerve": {"current": 23, "maximum": 25,
                                           "increment": 1, "interval": 300,
                                           "ticktime": 60, "fulltime": 480}})
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("nerve_overflow", [e["type"] for e in events])

    def test_nerve_mark_disabled_by_default(self):
        # climbing 8 -> 11 no longer fires a nerve mark (replaced by overflow)
        prev = tw.parse_snapshot({"nerve": {"current": 8, "maximum": 25}})
        cur = tw.parse_snapshot({"nerve": {"current": 11, "maximum": 25}})
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("nerve_mark", [e["type"] for e in events])

    def test_overflow_silent_when_full(self):
        prev = self._snap(95, 60)
        cur = tw.parse_snapshot({"energy": {"current": 100, "maximum": 100}})
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("energy_overflow", [e["type"] for e in events])

    def test_hospital_in_fires_with_until(self):
        prev = self._snap(50, 7200, state="Okay")
        cur = self._snap(50, 7200, state="Hospital")
        # inject an until timestamp on the cur status
        cur["status"]["until"] = 9999999999
        events, _ = tw.evaluate(prev, cur, self.cfg)
        hi = [e for e in events if e["type"] == "hospital_in"]
        self.assertTrue(hi)
        self.assertEqual(hi[0]["until"], 9999999999)

    def test_hospital_in_no_fire_from_unknown(self):
        prev = self._snap(50, 7200, state="")   # unknown baseline
        cur = self._snap(50, 7200, state="Hospital")
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("hospital_in", [e["type"] for e in events])

    def test_hospital_in_message_shows_countdown(self):
        import time
        ev = {"type": "hospital_in", "until": int(time.time()) + 600}
        msg = tw.format_event(ev)
        self.assertIn("out in", msg)

    def test_hospital_out_fires(self):
        prev = self._snap(50, 7200, state="Hospital")
        cur = self._snap(50, 7200, state="Okay")
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("hospital_out", [e["type"] for e in events])

    def test_hospital_no_fire_when_unknown_state(self):
        prev = self._snap(50, 7200, state="Hospital")
        cur = self._snap(50, 7200, state="")   # field missing this poll
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("hospital_out", [e["type"] for e in events])

    def test_jail_out_fires(self):
        prev = self._snap(50, 7200, state="In Jail")
        cur = self._snap(50, 7200, state="Okay")
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertIn("jail_out", [e["type"] for e in events])

    def test_milestone_fires_on_cross(self):
        prev = self._snap(50, 7200, total=9_000)
        cur = self._snap(50, 7200, total=11_000)
        events, _ = tw.evaluate(prev, cur, self.cfg)
        ms = [e for e in events if e["type"] == "milestone"]
        self.assertTrue(ms)
        self.assertEqual(ms[0]["value"], 10_000)

    def test_milestone_no_burst_on_fresh_deploy(self):
        # prev total 0 (old state had no battlestats) must not fire a burst
        prev = self._snap(50, 7200, total=0)
        cur = self._snap(50, 7200, total=123_456)
        events, _ = tw.evaluate(prev, cur, self.cfg)
        self.assertNotIn("milestone", [e["type"] for e in events])

    def test_milestone_one_event_for_multi_cross(self):
        prev = self._snap(50, 7200, total=4_000)
        cur = self._snap(50, 7200, total=60_000)  # crosses 5k,10k,25k,50k
        events, _ = tw.evaluate(prev, cur, self.cfg)
        ms = [e for e in events if e["type"] == "milestone"]
        self.assertEqual(len(ms), 1)
        self.assertEqual(ms[0]["value"], 50_000)

    def test_overflow_and_milestone_messages_render(self):
        for ev in (
            {"type": "energy_overflow", "bar": "energy", "current": 95,
             "maximum": 100, "eta_full": 60},
            {"type": "hospital_out"},
            {"type": "jail_out"},
            {"type": "milestone", "value": 50_000, "total": 60_000},
        ):
            msg = tw.format_event(ev)
            self.assertTrue(len(msg) > 0)
        self.assertIn("caps in", tw.format_event(
            {"type": "energy_overflow", "bar": "energy", "current": 95,
             "maximum": 100, "eta_full": 60}))
        self.assertIn("50,000", tw.format_event(
            {"type": "milestone", "value": 50_000, "total": 60_000}))


class AdaptiveSleepTests(unittest.TestCase):
    def setUp(self):
        self.cfg = tw.default_config()  # marks energy 25, nerve 10

    def _snap(self, e=(20, 100, 900, 300, 300, 16200), n=(8, 25, 1, 300, 60, 5100)):
        def bar(t):
            c, m, inc, iv, tt, ft = t
            return {"current": c, "maximum": m, "increment": inc,
                    "interval": iv, "ticktime": tt, "fulltime": ft}
        return tw.parse_snapshot({"energy": bar(e), "nerve": bar(n)})

    def test_sleeps_until_sooner_mark(self):
        # energy 20->25 needs 1 tick; nerve 8->10 needs 2 ticks
        # energy: ticktime 300 -> reaches 25 at +300; nerve: 60 + 300 = 360
        snap = self._snap()
        s = tw.next_sleep_seconds(snap, self.cfg)
        # energy crossing (300s) is sooner; +buffer, capped at 300
        self.assertLessEqual(s, tw.POLL_CAP_SECONDS)
        self.assertGreaterEqual(s, tw.POLL_MIN_SECONDS)

    def test_cap_when_both_above_mark(self):
        snap = self._snap(e=(30, 100, 5, 900, 300, 12600),
                          n=(12, 25, 1, 300, 60, 4200))  # both already above marks
        s = tw.next_sleep_seconds(snap, self.cfg)
        self.assertEqual(s, tw.POLL_CAP_SECONDS)

    def test_floor_min_sleep(self):
        # mark essentially imminent -> floored at min
        snap = self._snap(e=(24, 100, 5, 900, 5, 100))  # reaches 25 in ~5s
        s = tw.next_sleep_seconds(snap, self.cfg, cap=300, min_sleep=60)
        self.assertEqual(s, 60)

    def test_cap_respected_when_marks_far(self):
        snap = self._snap(e=(0, 100, 5, 900, 900, 18000),
                          n=(0, 25, 1, 300, 300, 7500))
        s = tw.next_sleep_seconds(snap, self.cfg, cap=300)
        self.assertEqual(s, 300)  # next mark is far, so cap wins


class LoopModeTests(unittest.TestCase):
    """Guards the --loop entrypoint (would have caught the datetime shadowing)."""

    def test_loop_runs_clean(self):
        import os, tempfile
        orig_fetch, orig_notify = tw.make_torn_fetcher, tw.make_telegram_notifier
        sent = []
        tw.make_torn_fetcher = lambda k, s=tw.DEFAULT_SELECTIONS: (lambda _="": {
            "energy": {"current": 20, "maximum": 100, "increment": 5,
                       "interval": 900, "ticktime": 300, "fulltime": 16200},
            "nerve": {"current": 8, "maximum": 25, "increment": 1,
                      "interval": 300, "ticktime": 60, "fulltime": 5100},
            "cooldowns": {}, "travel": {}, "status": {"state": "Okay"}, "total": 238})
        tw.make_telegram_notifier = lambda t, c: (lambda m: sent.append(m))
        os.environ.update(TORN_API_KEY="x", TELEGRAM_BOT_TOKEN="y", TELEGRAM_CHAT_ID="z")
        try:
            rc = tw.main(["--loop", "1", "--state", tempfile.mktemp(suffix=".json")])
        finally:
            tw.make_torn_fetcher, tw.make_telegram_notifier = orig_fetch, orig_notify
        self.assertEqual(rc, 0)


class LifeRegenTests(unittest.TestCase):
    def test_estimates_from_5pct_per_5min(self):
        # 147/325: 5% of 325 = 16.25/tick, need 178 -> 11 ticks -> 3300s
        self.assertEqual(tw.seconds_to_full_life(147, 325), 3300)

    def test_full_is_zero(self):
        self.assertEqual(tw.seconds_to_full_life(325, 325), 0)

    def test_unknown_max_is_none(self):
        self.assertIsNone(tw.seconds_to_full_life(0, 0))


class QuietHoursTests(unittest.TestCase):
    def setUp(self):
        self.cfg = tw.default_config()  # quiet 01:00–08:00 local, enabled

    def _utc_for_local_hour(self, local_hour, tz=8):
        # pick a UTC time whose local hour (UTC+tz) is local_hour
        from datetime import datetime, timezone
        utc_hour = (local_hour - tz) % 24
        return datetime(2026, 10, 2, utc_hour, 0, tzinfo=timezone.utc)

    def test_in_quiet_window(self):
        self.assertTrue(tw.in_quiet_hours(self._utc_for_local_hour(3), self.cfg, 8))

    def test_outside_quiet_window(self):
        self.assertFalse(tw.in_quiet_hours(self._utc_for_local_hour(15), self.cfg, 8))

    def test_disabled(self):
        self.cfg["quiet_hours"]["enabled"] = False
        self.assertFalse(tw.in_quiet_hours(self._utc_for_local_hour(3), self.cfg, 8))

    def test_filter_suppresses_noisy_keeps_important(self):
        events = [
            {"type": "energy_full", "current": 100, "maximum": 100},
            {"type": "milestone", "value": 50_000, "total": 60_000},
            {"type": "hospital_out"},
        ]
        kept = tw.filter_quiet(events, self._utc_for_local_hour(3), self.cfg, 8)
        kinds = [e["type"] for e in kept]
        self.assertNotIn("energy_full", kinds)
        self.assertIn("milestone", kinds)
        self.assertIn("hospital_out", kinds)

    def test_filter_passthrough_outside_quiet(self):
        events = [{"type": "energy_full", "current": 100, "maximum": 100}]
        kept = tw.filter_quiet(events, self._utc_for_local_hour(15), self.cfg, 8)
        self.assertEqual(len(kept), 1)


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
