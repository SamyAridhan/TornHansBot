"""Background jobs with fake clock / Telegram / Torn / feeds."""

import unittest

from hans import config as C
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
        self.assertEqual(ctx.store["travel"]["flights"]["chi"], 1500)
        # a mid-flight re-read (small drift) keeps the same arming
        clock.advance(100)
        torn.payload = flying("China", 1403)
        bars_job.run(ctx)
        self.assertEqual(ctx.store["landing"]["arrival"], T0 + 1500)
        self.assertEqual(ctx.store["travel"]["flights"]["chi"], 1500)

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
    MODEL = {"items": {"chi": {"274": {"interval": 2820, "last": T0 + 3000}}}}

    def make(self, now):
        ctx = make_ctx(clock=Clock(now), feeds=FakeFeeds(model=self.MODEL))
        ctx.store["travel"]["flights"] = {"chi": 1500}
        ctx.snapshot = {"travel": {"time_left": 0}}
        return ctx

    def test_waits_then_fires_once(self):
        dep = T0 + 3000 + 120 - 1500
        ctx = self.make(dep - 200)
        self.assertEqual(depart.run(ctx), C.DEPART_POLL)
        ctx.clock.t = dep - 40
        self.assertEqual(depart.run(ctx), 40)
        ctx.clock.t = dep + 1
        depart.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)
        self.assertIn("Leave for China now", ctx.tg.sent[0][1])
        ctx.clock.t = dep + 10
        depart.run(ctx)
        self.assertEqual(len(ctx.tg.sent), 1)          # no repeat

    def test_quiet_while_flying_or_disabled(self):
        dep = T0 + 3000 + 120 - 1500
        ctx = self.make(dep)
        ctx.snapshot = {"travel": {"time_left": 300}}
        depart.run(ctx)
        ctx.snapshot = {"travel": {"time_left": 0}}
        ctx.settings["depart"]["on"] = False
        depart.run(ctx)
        self.assertEqual(ctx.tg.sent, [])

    def test_no_flight_time_no_alert(self):
        ctx = self.make(T0 + 1620)
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
