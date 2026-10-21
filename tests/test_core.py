"""Pure logic: bars/alerts, messages, travel math, OC, stats, formatting."""

import unittest
from datetime import datetime, timezone

from hans.core import bars, fmt, messages, oc, stats, travel

from .fakes import bar, user_payload

T0 = 1_800_000_000


def snap(**over):
    return bars.parse_snapshot(user_payload(**over))


CFG = {
    "alerts": {"energy_full": True, "nerve_full": True, "happy_full": False,
               "drug_cooldown_ended": True, "medical_cooldown_ended": False,
               "booster_cooldown_ended": False, "energy_overflow": True,
               "nerve_overflow": True, "hospital_in": True, "hospital_out": True,
               "jail_in": True, "jail_out": True, "milestone": True},
    "marks": {"energy": 25, "nerve": None},
    "overflow_lead_seconds": 600,
    "milestones": [10_000, 25_000, 50_000],
    "quiet_hours": {"enabled": True, "start": 1, "end": 8},
}


def types(evs):
    return sorted(e["type"] for e in evs)


class ParseTests(unittest.TestCase):
    def test_fields(self):
        s = snap()
        self.assertEqual(s["energy"], {"current": 10, "maximum": 150})
        self.assertEqual(s["player_id"], 4538696)
        self.assertEqual(s["battle_total"], 20000)
        self.assertEqual(s["travel"]["destination"], "Torn")

    def test_partial_payload_is_safe(self):
        s = bars.parse_snapshot({})
        self.assertEqual(s["energy"]["current"], 0)
        self.assertEqual(s["status"]["state"], "")

    def test_battlestats(self):
        self.assertEqual(bars.parse_battlestats({"total": "7", "speed": 3})["total"], 7)


class TimingTests(unittest.TestCase):
    def test_seconds_to_value(self):
        reg = {"increment": 5, "interval": 900, "ticktime": 300}
        self.assertEqual(bars.seconds_to_value(20, 25, reg), 300)
        self.assertEqual(bars.seconds_to_value(20, 30, reg), 1200)
        self.assertEqual(bars.seconds_to_value(30, 25, reg), 0)
        self.assertIsNone(bars.seconds_to_value(1, 5, {}))

    def test_full_prefers_fulltime(self):
        self.assertEqual(bars.seconds_to_full(10, 100, {"fulltime": 99}), 99)
        self.assertEqual(bars.seconds_to_full(100, 100, {}), 0)

    def test_life_estimate(self):
        self.assertEqual(bars.seconds_to_full_life(300, 400), 1500)  # 20/tick -> 5 ticks x 5min
        self.assertEqual(bars.seconds_to_full_life(400, 400), 0)

    def test_next_sleep(self):
        s = snap(energy=bar(20, 150, inc=5, interval=600, tick=100))
        self.assertEqual(bars.next_sleep_seconds(s, CFG, cap=300, min_sleep=30), 108)
        s2 = snap(energy=bar(30, 150))
        self.assertEqual(bars.next_sleep_seconds(s2, CFG, cap=120, min_sleep=30), 120)


class EvaluateTests(unittest.TestCase):
    def test_baseline_silent(self):
        self.assertEqual(bars.evaluate(None, snap(), CFG), [])

    def test_energy_full_edge(self):
        prev = snap(energy=bar(140, 150))
        cur = snap(energy=bar(150, 150))
        self.assertIn("energy_full", types(bars.evaluate(prev, cur, CFG)))
        self.assertNotIn("energy_full", types(bars.evaluate(cur, cur, CFG)))

    def test_mark_crossing_once(self):
        prev, cur = snap(energy=bar(20, 150)), snap(energy=bar(25, 150, full=7000))
        evs = bars.evaluate(prev, cur, CFG)
        mark = [e for e in evs if e["type"] == "energy_mark"][0]
        self.assertEqual(mark["eta_full"], 7000)
        self.assertEqual(bars.evaluate(cur, cur, CFG), [])

    def test_cooldown_end(self):
        evs = bars.evaluate(snap(cooldowns={"drug": 30}), snap(cooldowns={"drug": 0}), CFG)
        self.assertEqual(types(evs), ["drug_cooldown_ended"])

    def test_overflow_enters_window(self):
        prev = snap(nerve=bar(20, 25, inc=1, interval=300, full=1500))
        cur = snap(nerve=bar(23, 25, inc=1, interval=300, full=500))
        self.assertIn("nerve_overflow", types(bars.evaluate(prev, cur, CFG)))

    def test_hospital_in_out(self):
        ok = snap(status={"state": "Okay", "until": 0})
        hosp = snap(status={"state": "Hospital", "until": T0 + 600})
        self.assertEqual(types(bars.evaluate(ok, hosp, CFG)), ["hospital_in"])
        self.assertEqual(types(bars.evaluate(hosp, ok, CFG)), ["hospital_out"])
        self.assertEqual(types(bars.evaluate(snap(status={}), hosp, CFG)), [])  # unknown prev

    def test_milestone(self):
        evs = bars.evaluate(snap(total=9000), snap(total=26000), CFG)
        self.assertEqual(evs, [{"type": "milestone", "value": 25000, "total": 26000}])
        self.assertEqual(bars.evaluate(snap(total=0), snap(total=26000), CFG), [])

    def test_disabled_alert(self):
        cfg = {**CFG, "alerts": {**CFG["alerts"], "energy_full": False}}
        self.assertEqual(bars.evaluate(snap(energy=bar(140, 150)),
                                       snap(energy=bar(150, 150)), cfg), [])


class QuietTests(unittest.TestCase):
    def test_window(self):
        at = lambda h: datetime(2026, 10, 20, (h - 8) % 24, tzinfo=timezone.utc)  # noqa: E731
        self.assertTrue(bars.in_quiet_hours(at(3), CFG, 8))
        self.assertFalse(bars.in_quiet_hours(at(9), CFG, 8))
        wrap = {"quiet_hours": {"enabled": True, "start": 23, "end": 7}}
        self.assertTrue(bars.in_quiet_hours(at(23), wrap, 8))
        self.assertTrue(bars.in_quiet_hours(at(2), wrap, 8))

    def test_filter_keeps_important(self):
        night = datetime(2026, 10, 19, 19, tzinfo=timezone.utc)   # 03:00 local
        evs = [{"type": "energy_full"}, {"type": "hospital_out"}]
        self.assertEqual(bars.filter_quiet(evs, night, CFG, 8), [{"type": "hospital_out"}])


class MessageTests(unittest.TestCase):
    def test_bar_event_has_gauge_and_eta(self):
        txt = messages.format_event({"type": "energy_mark", "bar": "energy", "current": 25,
                                     "maximum": 100, "eta_full": 3600})
        self.assertIn("25/100", txt)
        self.assertIn("full in <b>1h</b>", txt)

    def test_hospital_countdown(self):
        txt = messages.format_event({"type": "hospital_in", "until": T0 + 720}, now=T0)
        self.assertIn("12m", txt)

    def test_unknown(self):
        self.assertEqual(messages.format_event({"type": "x<y"}), "• x&lt;y")


class FmtTests(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(fmt.fmt_duration(3900), "1h 05m")
        self.assertEqual(fmt.fmt_duration_compact(3900), "1h05m")
        self.assertEqual(fmt.fmt_duration_compact(0), "now")
        self.assertEqual(fmt.fmt_duration_compact(None), "?")

    def test_clock_tz(self):
        self.assertEqual(fmt.clock(datetime(2026, 1, 1, 7, 40, tzinfo=timezone.utc)
                                   .timestamp(), 8), "15:40")


SAMPLE = {"stocks": {
    "chi": {"update": 1000, "stocks": [
        {"id": 274, "name": "Panda Plushie", "quantity": 883, "cost": 400, "nextRestock": 2000},
        {"id": 999, "name": "Peony", "quantity": 50, "cost": 5000, "nextRestock": None}]},
    "MEX": {"update": 1000, "stocks": [
        {"id": 258, "name": "Jaguar Plushie", "quantity": 0, "cost": 10000,
         "nextRestock": "1970-01-01T00:33:20+00:00"}]},
}}
MODEL = {"items": {"chi": {"274": {"interval": 2820, "last": 100000}}}}


class TravelTests(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(travel.code_for("China"), "chi")
        self.assertEqual(travel.code_for("Cayman Islands"), "cay")
        self.assertEqual(travel.code_for("United Kingdom"), "uni")
        self.assertIsNone(travel.code_for("atlantis"))

    def test_foreign(self):
        self.assertTrue(travel.is_foreign("Mexico"))
        self.assertFalse(travel.is_foreign("Torn"))
        self.assertFalse(travel.is_foreign(""))

    def test_parse_and_find(self):
        p = travel.parse_stock(SAMPLE)
        self.assertEqual(p["mex"]["items"][0]["restock"], 2000)
        self.assertEqual(travel.find_item(p, "chi", "panda")["qty"], 883)
        self.assertEqual(travel.find_item(p, "chi", "")["name"], "Peony")
        self.assertIsNone(travel.find_item(p, "chi", "nope"))

    def test_restock_epoch_forms(self):
        self.assertEqual(travel._restock_epoch(2_000_000_000_000), 2_000_000_000)
        self.assertIsNone(travel._restock_epoch("garbage"))

    def test_depart_math(self):
        self.assertEqual(travel.depart_epoch(10000, 3600, 120), 6520)
        self.assertEqual(travel.next_catchable_depart(10000, 3600, 6000), 6520)
        self.assertIsNone(travel.next_catchable_depart(10000, 3600, 9000))
        self.assertEqual(travel.next_catchable_depart(10000, 3600, 7000, 2820), 9340)

    def test_model(self):
        self.assertEqual(travel.model_interval_last(MODEL, "chi", 274), (2820, 100000))
        self.assertEqual(travel.next_restock(100000, 2820, 104000), 105640)
        self.assertIsNone(travel.next_restock(0, 2820, 1))

    def test_flights_median_per_ticket_type(self):
        fl = {"chi": 13000}                        # old single-number format still works
        self.assertEqual(travel.flight_estimate(fl, "chi")["sec"], 13000)
        for sec in (13020, 13260, 13800, 13100):
            travel.record_flight(fl, "chi", sec, "Standard")
        est = travel.flight_estimate(fl, "chi")
        self.assertEqual(est["sec"], 13260)        # median of the standard trips
        self.assertEqual(est["n"], 4)
        self.assertEqual((est["lo"], est["hi"]), (int(13260 * .97), int(13260 * 1.03)))
        travel.record_flight(fl, "chi", 9200, "Airstrip")
        self.assertEqual(travel.flight_estimate(fl, "chi")["sec"], 9200)   # latest type
        travel.record_flight(fl, "chi", 5, "x")                           # junk ignored
        self.assertEqual(len(fl["chi"]), 6)

    def test_stock_tracker_learns_delay_and_rate(self):
        tr, it = {}, {"id": 274, "qty": 100}
        travel.observe_stock(tr, "chi", it, 1000)
        travel.observe_stock(tr, "chi", {"id": 274, "qty": 40}, 1060)     # 1 item/s
        t = tr["chi:274"]
        self.assertAlmostEqual(t["rate"], 1.0)
        travel.observe_stock(tr, "chi", {"id": 274, "qty": 0}, 1120)
        self.assertEqual(t["zero_at"], 1090)                             # midpoint
        travel.observe_stock(tr, "chi", {"id": 274, "qty": 0}, 1500)
        travel.observe_stock(tr, "chi", {"id": 274, "qty": 900}, 1960)
        self.assertEqual(t["delays"], [840])                             # 1930 - 1090
        self.assertEqual(t["restock_qty"], 900)
        self.assertEqual(travel.restock_delay(t), (840, True))
        self.assertEqual(travel.restock_delay({}), (travel.DEFAULT_DELAY, False))

    def test_predict_restock_bases(self):
        t = {"zero_at": 5000, "delays": [600], "rate": 2.0, "restock_qty": 800}
        p = travel.predict_restock(t, 0, {}, "chi", 274, now=5100)
        self.assertEqual((p["basis"], p["restock"], p["cycle"]), ("empty", 5600, 600 + 400))
        p = travel.predict_restock(t, 200, {}, "chi", 274, now=5100)
        self.assertEqual((p["basis"], p["empties"], p["restock"]), ("selling", 5200, 5800))
        p = travel.predict_restock({}, 50, MODEL, "chi", 274, now=104000)
        self.assertEqual((p["basis"], p["restock"]), ("model", 105640))

    def test_plan_short_and_long_flights(self):
        stock = {"mex": {"items": [{"id": 258, "name": "Jaguar Plushie", "qty": 600,
                                    "cost": 1, "restock": None}]}}
        track = {"mex:258": {"delays": [900], "rate": 1.0, "restock_qty": 1500}}
        fl = {"mex": [[1380, "Standard"]]}
        p = travel.plan("mex", "jaguar", stock, {}, fl, track, now=10000)
        # sells out at 10600, restocks 900s later, leave so you land 2 min after
        self.assertEqual(p["depart"], 10600 + 900 + 120 - 1380)
        self.assertFalse(p["too_long"])
        self.assertLess(p["error"], 240)
        far = {"chi": [[13000, "Standard"]]}
        stock2 = {"chi": {"items": [{"id": 274, "name": "Panda Plushie", "qty": 0,
                                     "cost": 1, "restock": None}]}}
        track2 = {"chi:274": {"zero_at": 10000, "delays": [900], "rate": 1.0,
                              "restock_qty": 1500}}
        p2 = travel.plan("chi", "panda", stock2, {}, far, track2, now=10100)
        self.assertIsNone(p2["depart"])            # would need guessing several sell-outs
        self.assertTrue(p2["too_long"])
        p3 = travel.plan("chi", "panda", stock2, MODEL, far, {}, now=104000)
        self.assertIsNone(p3["depart"])            # no live sell-out anchor -> no time
        self.assertIsNone(travel.plan("chi", "panda", stock2, {}, {}, track2, 10100)["depart"])


class OcTests(unittest.TestCase):
    PAYLOAD = {"crimes": [
        {"status": "Planning", "ready_at": T0 + 50000, "slots": [{"user_id": 1}]},
        {"status": "Planning", "ready_at": T0 + 40000, "slots": [{"user": {"id": 7}}]},
        {"status": "Successful", "ready_at": T0 + 100, "slots": [{"user_id": 7}]},
    ]}

    def test_parse_mine(self):
        self.assertEqual(oc.parse_my_oc_ready(self.PAYLOAD, 7), T0 + 40000)
        self.assertIsNone(oc.parse_my_oc_ready(self.PAYLOAD, 99))

    def test_due(self):
        ready = T0 + 11 * 3600
        self.assertEqual(oc.due_reminders(ready, T0), [86400, 43200])
        self.assertEqual(oc.due_reminders(ready, T0, fired=[86400, 43200]), [])
        self.assertEqual(oc.due_reminders(T0 - 5, T0), [])

    def test_parse_when(self):
        self.assertEqual(oc.parse_when("in 12h", T0, 8), T0 + 43200)
        self.assertEqual(oc.parse_when("90m", T0, 8), T0 + 5400)
        hm = oc.parse_when("23:59", T0, 8)
        self.assertTrue(T0 < hm <= T0 + 86400)
        self.assertIsNone(oc.parse_when("whenever", T0, 8))


class StatsTests(unittest.TestCase):
    ROWS = [{"timestamp": f"2026-10-{d:02d}T02:00:00+00:00", "total": str(t)}
            for d, t in ((10, 100), (11, 150), (12, 200), (13, 260))]

    def test_daily_and_streak(self):
        daily = stats.daily_last_totals(self.ROWS, 8)
        self.assertEqual(daily[-1], ("2026-10-13", 260))
        self.assertEqual(stats.current_streak(daily), 3)
        self.assertEqual(stats.gain_last_n_days(daily, 7), 160)

    def test_digest_text(self):
        s = bars.parse_snapshot(user_payload())
        txt = stats.format_digest(s, {"total": 260}, stats.daily_last_totals(self.ROWS),
                                  datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc), 8)
        self.assertIn("Morning, boss", txt)
        self.assertIn("+60 day-on-day", txt)
        self.assertIn("Streak", txt)

    def test_series_and_weekly(self):
        series = stats.load_series(self.ROWS + [{"timestamp": "bad", "total": "1"}])
        self.assertEqual(len(series), 4)
        self.assertEqual(stats.weekly_gain(series), 160)

    def test_history_roundtrip(self):
        import os
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "h.csv")
        stats.append_history(p, "2026-10-01T00:00:00+00:00", {"total": 5}, 25)
        stats.append_history(p, "2026-10-02T00:00:00+00:00", {"total": 9}, 25)
        rows = stats.read_history(p)
        self.assertEqual([r["total"] for r in rows], ["5", "9"])

    def test_market(self):
        raw = {"itemmarket": {"item": {"name": "Xanax", "image": "https://x/y.png"},
                              "listings": [{"price": 900}, {"price": 820}]}}
        self.assertEqual(stats.parse_market(raw), 820)
        self.assertEqual(stats.parse_market_item(raw)["name"], "Xanax")
        self.assertIsNone(stats.parse_market({"itemmarket": {"listings": []}}))


if __name__ == "__main__":
    unittest.main()


class EmptyShareTests(unittest.TestCase):
    def test_counts(self):
        tr = {}
        for i in range(40):
            travel.observe_stock(tr, "chi", {"id": 274, "qty": 0 if i % 4 == 0 else 10}, i * 60)
        p = travel.plan("chi", "", {"chi": {"items": [{"id": 274, "name": "P", "qty": 10,
                                                      "cost": 1}]}}, {}, {}, tr, 2400)
        self.assertAlmostEqual(p["empty_share"], 0.25)


class BestWindowTests(unittest.TestCase):
    def test_best_window(self):
        t = {"restocked_at": 10000, "gaps": [2400, 2400, 2400]}
        self.assertEqual(travel.best_window(t, 10000), 10000 + int(0.72 * 2400))
        self.assertEqual(travel.best_window(t, 13000), 10000 + int(0.72 * 2400) + 2400)
        self.assertIsNone(travel.best_window(t, 10000 + 3 * 2400))   # stale phase
        self.assertIsNone(travel.best_window({}, 10000))

    def test_tracker_learns_gaps(self):
        tr = {}
        for when, q in ((0, 0), (60, 900), (600, 0), (2460, 900)):
            travel.observe_stock(tr, "chi", {"id": 274, "qty": q}, when)
        self.assertEqual(tr["chi:274"]["gaps"], [2400])
