"""Background jobs with fake clock / Telegram / Torn / feeds."""

import unittest

from hans import config as C
from hans.core import travel
from hans.jobs import bars as bars_job, depart, landing, oc as oc_job, pin, schedule

from .fakes import Clock, FakeFeeds, FakeTorn, bar, make_ctx, user_payload

# 2027-01-15 08:00:00 UTC = 16:00 local (Friday)
T0 = 1_800_000_000


def flying(dest, left):
    return user_payload(travel={"time_left": left, "destination": dest},
                        status={"state": "Traveling", "until": 0})


class BarsJobTests(unittest.TestCase):
    def test_first_pass_is_silent_then_alerts(self):
        torn = FakeTorn(user_payload(energy=bar(140, 150)))
        ctx = make_ctx(torn=torn)
        bars_job.run(ctx)
        self.assertEqual(ctx.tg.sent, [])
        torn.payload = user_payload(energy=bar(150, 150))
        bars_job.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)
        self.assertIn("Energy", ctx.tg.sent[0][1])

    def test_disabled_in_settings(self):
        torn = FakeTorn(user_payload(energy=bar(140, 150)))
        ctx = make_ctx(torn=torn)
        ctx.settings["alerts"]["energy_full"] = False
        bars_job.run(ctx)
        torn.payload = user_payload(energy=bar(150, 150))
        bars_job.run(ctx)
        self.assertEqual(ctx.tg.sent, [])

    def test_quiet_hours_hold_routine_pings(self):
        clock = Clock(T0 + 11 * 3600)          # 03:00 local
        torn = FakeTorn(user_payload(energy=bar(140, 150)))
        ctx = make_ctx(torn=torn, clock=clock)
        bars_job.run(ctx)
        torn.payload = user_payload(energy=bar(150, 150))
        bars_job.run(ctx)
        self.assertEqual(ctx.tg.sent, [])

    def test_poll_delay_bounds(self):
        ctx = make_ctx()
        d = bars_job.run(ctx)
        self.assertTrue(C.BARS_POLL_MIN <= d <= C.BARS_POLL_MAX)

    def test_arms_landing_and_learns_flight(self):
        clock = Clock(T0)
        torn = FakeTorn(flying("China", 1500))
        ctx = make_ctx(torn=torn, clock=clock)
        bars_job.run(ctx)
        self.assertEqual(ctx.store["landing"]["arrival"], T0 + 1500)
        clock.advance(100)
        torn.payload = flying("China", 1400)
        bars_job.run(ctx)
        self.assertEqual(ctx.store["landing"]["arrival"], T0 + 1500)   # same arming
        clock.advance(1450)
        torn.payload = user_payload(travel={"time_left": 0, "destination": "China"})
        bars_job.run(ctx)
        self.assertEqual(ctx.store["travel"]["flights"]["chi"], [[1500, ""]])

    def test_exact_flight_from_api_stamps(self):
        clock = Clock(T0)
        p = flying("China", 1500)
        p["travel"].update(departed=T0 - 200, timestamp=T0 + 1500, method="Airstrip")
        ctx = make_ctx(torn=FakeTorn(p), clock=clock)
        bars_job.run(ctx)
        clock.advance(1600)
        ctx.torn.payload = user_payload(travel={"time_left": 0, "destination": "China"})
        bars_job.run(ctx)
        self.assertEqual(ctx.store["travel"]["flights"]["chi"], [[1700, "Airstrip"]])

    def test_ignores_stamps_that_disagree(self):
        p = flying("China", 1500)
        p["travel"].update(departed=5, timestamp=T0 + 99999)
        ctx = make_ctx(torn=FakeTorn(p), clock=Clock(T0))
        bars_job.run(ctx)
        self.assertEqual(ctx.store["landing"]["arrival"], T0 + 1500)
        self.assertEqual(ctx.store["travel"]["trip"]["flight"], 1500)

    def test_back_in_torn_ping_once_on_time(self):
        clock = Clock(T0)
        ctx = make_ctx(torn=FakeTorn(flying("Torn", 600)), clock=clock)
        bars_job.run(ctx)
        self.assertEqual(landing.run(ctx), 300)
        clock.t = T0 + 590
        self.assertEqual(landing.run(ctx), 10)          # wakes right at landing
        clock.t = T0 + 600
        landing.run(ctx)
        landing.run(ctx)
        self.assertEqual(ctx.tg.texts(), ["🏠 <b>You're back in Torn.</b>"])
        ctx.settings["landing"]["home"] = False
        ctx.torn.payload = flying("Torn", 100)
        bars_job.run(ctx)
        clock.advance(100)
        landing.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)            # toggle respected

    def test_trip_home_never_arms(self):
        ctx = make_ctx(torn=FakeTorn(flying("Torn", 1500)))
        bars_job.run(ctx)
        self.assertEqual(ctx.store["landing"], {})

    def test_clears_after_landing(self):
        clock = Clock(T0)
        torn = FakeTorn(flying("Mexico", 600))
        ctx = make_ctx(torn=torn, clock=clock)
        bars_job.run(ctx)
        ctx.store["landing"]["alerted"] = True
        clock.advance(620)
        torn.payload = user_payload(travel={"time_left": 0, "destination": "Mexico"})
        bars_job.run(ctx)
        self.assertEqual(ctx.store["landing"], {})


class LogbookTests(unittest.TestCase):
    def test_restock_events_and_trip_are_logged(self):
        from hans import logbook
        clock = Clock(T0)
        stock = {"chi": {"items": [{"id": 274, "name": "Panda Plushie", "qty": 50,
                                    "cost": 1, "restock": None}]}}
        torn = FakeTorn(flying("China", 600))
        ctx = make_ctx(torn=torn, clock=clock, feeds=FakeFeeds(stock=stock))
        ctx.store["travel"]["suggested"] = {"chi": {str(T0 - 1): "best_odds"}}
        depart.run(ctx)
        bars_job.run(ctx)                               # take-off seen
        clock.advance(60)
        stock["chi"]["items"][0]["qty"] = 0             # sells out mid-flight
        depart.run(ctx)
        clock.advance(600)
        torn.payload = user_payload(travel={"time_left": 0, "destination": "China"})
        bars_job.run(ctx)                               # landed: still empty
        rs = logbook.read(logbook.RESTOCKS, ctx.cfg.log_dir)
        self.assertEqual([r["event"] for r in rs], ["sold_out"])
        trips = logbook.read(logbook.TRIPS, ctx.cfg.log_dir)
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0]["country"], "chi")
        self.assertEqual(trips[0]["shelf_at_landing"], "empty")
        self.assertEqual(ctx.store["travel"]["trips"][-1]["shelf"], "empty")
        self.assertEqual(trips[0]["hint"], "yes")             # left on Hans's suggested time
        self.assertEqual(trips[0]["hint_kind"], "best_odds")

    def test_shelf_at(self):
        t = {"qty": 100, "restocked_at": 500, "zero_at": None}
        self.assertTrue(travel.shelf_at(t, 600))        # restocked before landing
        self.assertFalse(travel.shelf_at(t, 400))       # restocked after landing
        t0 = {"qty": 0, "zero_at": 700}
        self.assertTrue(travel.shelf_at(t0, 600))       # sold out after landing
        self.assertFalse(travel.shelf_at(t0, 800))
        self.assertIsNone(travel.shelf_at({}, 1))


class LandingTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(T0)
        self.ctx = make_ctx(clock=self.clock)
        self.ctx.store["landing"].update({"arrival": T0 + 600, "dest": "China",
                                          "alerted": False})

    def test_sleeps_until_lead_then_buzzes(self):
        d = landing.run(self.ctx)
        self.assertEqual(d, 300)                       # capped wait
        self.clock.t = T0 + 600 - 60 - 10
        self.assertEqual(landing.run(self.ctx), 10)    # wakes exactly at lead
        self.clock.t = T0 + 540
        self.assertEqual(landing.run(self.ctx), 0)     # arms the buzz
        self.assertTrue(self.ctx.store["landing"]["alerted"])
        self.assertEqual(landing.run(self.ctx), C.BUZZ_INTERVAL)
        self.assertIn("landing in 60s", self.ctx.tg.sent[-1][1])
        self.assertEqual(self.ctx.tg.sent[-1][2]["inline_keyboard"][0][0]["callback_data"], "ack")

    def test_custom_lead(self):
        self.ctx.settings["landing"]["lead"] = 120
        self.clock.t = T0 + 480
        self.assertEqual(landing.run(self.ctx), 0)

    def test_off_never_buzzes(self):
        self.ctx.settings["landing"]["on"] = False
        self.clock.t = T0 + 590
        landing.run(self.ctx)
        self.assertIsNone(self.ctx.buzz)

    def test_says_landed_after_touchdown_and_times_out_with_cleanup(self):
        self.clock.t = T0 + 540
        landing.run(self.ctx)
        self.clock.t = T0 + 605
        landing.run(self.ctx)
        self.assertIn("LANDED", self.ctx.tg.sent[-1][1])
        self.clock.t = T0 + 600 + C.BUZZ_AFTER_LANDING + 1
        landing.run(self.ctx)
        self.assertIsNone(self.ctx.buzz)
        deletes = [p for m, p in self.ctx.tg.calls if m == "deleteMessages"]
        self.assertEqual(len(deletes[0]["message_ids"]), 1)
        self.assertIn("Buzz stopped", self.ctx.tg.sent[-1][1])
        self.assertEqual(self.ctx.store["landing"], {})

    def test_missed_landing_is_dropped(self):
        self.clock.t = T0 + 700
        landing.run(self.ctx)
        self.assertIsNone(self.ctx.buzz)
        self.assertEqual(self.ctx.store["landing"], {})


class DepartTests(unittest.TestCase):
    def make(self, now, flight=1380):
        stock = {"mex": {"items": [{"id": 258, "name": "Jaguar Plushie", "qty": 0,
                                    "cost": 1, "restock": None}]},
                 "chi": {"items": [{"id": 274, "name": "Panda Plushie", "qty": 0,
                                    "cost": 1, "restock": None}]}}
        ctx = make_ctx(clock=Clock(now), feeds=FakeFeeds(stock=stock))
        for code in ("mex", "chi"):
            ctx.settings["depart"]["routes"][code]["enabled"] = code == "mex"
        ctx.store["travel"]["flights"] = {"mex": [[flight, ""]], "chi": [[13000, ""]]}
        ctx.store["travel"]["stock"] = {
            "mex:258": {"qty": 0, "at": now, "zero_at": T0, "delays": [1800]},
            "chi:274": {"qty": 0, "at": now, "zero_at": T0, "delays": [900]}}
        ctx.snapshot = {"travel": {"time_left": 0}}
        return ctx

    def test_waits_then_fires_once(self):
        dep = T0 + 1800 + 120 - 1380
        ctx = self.make(dep - 200)
        self.assertEqual(depart.run(ctx), C.STOCK_POLL)
        ctx.clock.t = dep - 40
        self.assertEqual(depart.run(ctx), 40)
        ctx.clock.t = dep + 1
        depart.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)
        self.assertIn("Leave for Mexico now", ctx.tg.sent[0][1])
        ctx.clock.t = dep + 10
        depart.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)          # no repeat

    def test_long_flight_gets_best_odds_hint_not_exact_time(self):
        ctx = self.make(T0 + 60)
        ctx.settings["depart"]["routes"]["mex"]["enabled"] = False
        ctx.settings["depart"]["routes"]["chi"]["enabled"] = True
        ctx.store["travel"]["stock"]["chi:274"].update(restocked_at=T0, gaps=[2400])
        ctx.clock.t = T0 + int(0.72 * 2400)          # 16:28 local, not quiet hours
        depart.run(ctx)
        self.assertIn("Best time to leave for China", ctx.tg.texts()[-1])
        self.assertIn("~70%", ctx.tg.texts()[-1])

    def test_best_odds_hint_respects_quiet_hours(self):
        ctx = self.make(T0 + 11 * 3600)                # 03:00 local
        ctx.settings["depart"]["routes"]["mex"]["enabled"] = False
        ctx.settings["depart"]["routes"]["chi"]["enabled"] = True
        ctx.store["travel"]["stock"]["chi:274"].update(
            restocked_at=T0 + 11 * 3600 - int(0.72 * 2400), gaps=[2400])
        depart.run(ctx)
        self.assertEqual(ctx.tg.sent, [])

    def test_quiet_while_flying_or_disabled(self):
        dep = T0 + 1800 + 120 - 1380
        ctx = self.make(dep)
        ctx.snapshot = {"travel": {"time_left": 300}}
        depart.run(ctx)
        ctx.snapshot = {"travel": {"time_left": 0}}
        ctx.settings["depart"]["on"] = False
        depart.run(ctx)
        self.assertEqual(ctx.tg.sent, [])

    def test_learns_from_live_stock_and_alerts_after_sellout(self):
        clock = Clock(T0)
        stock = {"mex": {"items": [{"id": 258, "name": "Jaguar Plushie", "qty": 60,
                                    "cost": 1, "restock": None}]}}
        ctx = make_ctx(clock=clock, feeds=FakeFeeds(stock=stock))
        ctx.settings["depart"]["routes"]["chi"]["enabled"] = False
        ctx.settings["depart"]["routes"]["mex"]["enabled"] = True
        ctx.store["travel"]["flights"] = {"mex": [[1380, "Standard"]]}
        ctx.snapshot = {"travel": {"time_left": 0}}
        depart.run(ctx)
        clock.advance(60)
        stock["mex"]["items"][0]["qty"] = 0                       # sold out
        depart.run(ctx)
        zero = ctx.store["travel"]["stock"]["mex:258"]["zero_at"]
        dep = zero + travel.DEFAULT_DELAY + 120 - 1380            # 15m default delay
        clock.t = dep + 1
        depart.run(ctx)
        self.assertIn("Leave for Mexico now", ctx.tg.sent[-1][1])
        self.assertIn("sold out at", ctx.tg.sent[-1][1])

    def test_no_flight_time_no_alert(self):
        ctx = self.make(T0 + 540)
        ctx.store["travel"]["flights"] = {}
        depart.run(ctx)
        self.assertEqual(ctx.tg.sent, [])


class OcJobTests(unittest.TestCase):
    def test_auto_read_and_remind(self):
        crimes = {"crimes": [{"status": "Planning", "ready_at": T0 + 11 * 3600,
                              "slots": [{"user_id": 4538696}]}]}
        ctx = make_ctx(torn=FakeTorn(crimes=crimes), clock=Clock(T0))
        ctx.snapshot = {"player_id": 4538696}
        oc_job.run(ctx)
        self.assertEqual(ctx.store["oc"]["source"], "auto")
        self.assertEqual(len(ctx.tg.sent), 2)           # 24h and 12h both due
        oc_job.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 2)           # not repeated

    def test_manual_wins_and_reminders_toggle(self):
        ctx = make_ctx(torn=FakeTorn(crimes={"crimes": []}), clock=Clock(T0))
        oc_job.set_manual(ctx, T0 + 7 * 3600)
        ctx.settings["oc"]["on"] = False
        oc_job.run(ctx)
        self.assertEqual(ctx.tg.sent, [])
        ctx.settings["oc"]["on"] = True
        oc_job.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 3)
        self.assertEqual(ctx.store["oc"]["source"], "manual")

    def test_forgets_finished_oc(self):
        ctx = make_ctx(clock=Clock(T0))
        oc_job.set_manual(ctx, T0 - 4000)
        oc_job.run(ctx)
        self.assertEqual(ctx.store["oc"], {})


class ScheduleTests(unittest.TestCase):
    def test_seeds_then_sends_next_morning(self):
        clock = Clock(T0)                               # 16:00 local
        ctx = make_ctx(clock=clock)
        schedule.run(ctx)
        self.assertEqual(ctx.tg.sent, [])               # seeded, no stale send
        clock.t = T0 + 16 * 3600 + 30 * 60 - 60         # next day 08:29 local
        schedule.run(ctx)
        self.assertEqual(ctx.tg.sent, [])
        clock.advance(60)                               # 08:30
        schedule.run(ctx)
        self.assertIn("Morning, boss", ctx.tg.sent[-1][1])
        schedule.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)

    def test_digest_time_setting(self):
        clock = Clock(T0)
        ctx = make_ctx(clock=clock)
        schedule.run(ctx)
        ctx.settings["digest"]["time"] = "07:00"
        clock.t = T0 + 15 * 3600                        # 07:00 local next day
        schedule.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)


class PinTests(unittest.TestCase):
    def test_creates_pins_then_edits(self):
        ctx = make_ctx()
        pin.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)
        self.assertEqual(ctx.tg.pinned, [ctx.store["tg"]["pin_id"]])
        ctx.clock.advance(120)
        pin.run(ctx)
        self.assertEqual(len(ctx.tg.edits), 1)

    def test_waits_while_user_navigates(self):
        ctx = make_ctx()
        ctx.store["tg"].update({"pin_id": 5, "pin_view": "set", "pin_view_at": ctx.now()})
        pin.run(ctx)
        self.assertEqual(ctx.tg.edits, [])
        ctx.clock.advance(C.PIN_IDLE_RESET + 1)
        pin.run(ctx)
        self.assertEqual(ctx.tg.edits[-1][0], 5)
        self.assertEqual(ctx.store["tg"]["pin_view"], "home")

    def test_recreates_when_edit_fails(self):
        ctx = make_ctx()
        ctx.store["tg"]["pin_id"] = 5
        ctx.tg.edit_ok = False
        pin.run(ctx)
        self.assertNotEqual(ctx.store["tg"]["pin_id"], 5)
        self.assertEqual(len(ctx.tg.pinned), 1)


if __name__ == "__main__":
    unittest.main()
