"""Router, screens and buttons, plus a simulated run of the whole loop."""

import unittest

from hans import app, config as C, settings as S
from hans.ui import keyboards as K, router, views as V

from .fakes import Clock, FakeTorn, make_ctx, user_payload

T0 = 1_800_000_000


def tap(data, mid=7, chat=42, cq="c1"):
    return {"update_id": 1, "callback_query": {
        "id": cq, "data": data, "message": {"message_id": mid, "chat": {"id": chat}}}}


def msg(text, chat=42, uid=1):
    return {"update_id": uid, "message": {"text": text, "chat": {"id": chat}}}


def all_callback_data(markup):
    return [b["callback_data"] for row in markup["inline_keyboard"] for b in row]


class ParseTests(unittest.TestCase):
    def test_commands(self):
        self.assertEqual(router.parse_command("/start"), ("menu", ""))
        self.assertEqual(router.parse_command("/status@HansBot"), ("menu", ""))
        self.assertEqual(router.parse_command("/stock china panda"), ("stock", "china panda"))
        self.assertEqual(router.parse_command("buzz"), ("testland", ""))
        self.assertEqual(router.parse_command("hello"), (None, ""))
        self.assertEqual(router.parse_command(""), (None, ""))


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx(clock=Clock(T0))

    def test_every_button_fits_telegram_limit(self):
        s = self.ctx.settings
        boards = [K.HOME, K.ACK, K.stats(), K.oc(), K.travel_menu(s["depart"]["routes"]),
                  K.route("chi", True), K.settings_home()] + \
                 [K.settings_page(p, s) for p in S.PAGES]
        for b in boards:
            for data in all_callback_data(b):
                self.assertLessEqual(len(data.encode()), 64)

    def test_every_button_is_handled(self):
        """Tap every button on every screen: none may crash or be ignored."""
        s = self.ctx.settings
        boards = [K.HOME, K.stats(), K.oc(), K.travel_menu(s["depart"]["routes"]),
                  K.route("chi", True), K.settings_home()] + \
                 [K.settings_page(p, s) for p in S.PAGES]
        for b in boards:
            for data in all_callback_data(b):
                if data == "run:buzz":
                    continue
                router.handle_update(self.ctx, tap(data))
        self.assertTrue(self.ctx.tg.edits)
        bad = [t for _m, t, _k in self.ctx.tg.edits if "Couldn't load" in t]
        self.assertEqual(bad, [])

    def test_nav_edits_in_place(self):
        router.handle_update(self.ctx, tap("nav:travel"))
        mid, text, markup = self.ctx.tg.edits[-1]
        self.assertEqual(mid, 7)
        self.assertIn("Travel", text)
        self.assertIn("stk:chi", all_callback_data(markup))
        self.assertEqual(self.ctx.tg.answers[-1], ("c1", ""))

    def test_settings_toggle_persists_and_redraws(self):
        router.handle_update(self.ctx, tap("set:hf:alerts"))
        self.assertTrue(self.ctx.settings["alerts"]["happy_full"])
        _mid, _text, markup = self.ctx.tg.edits[-1]
        labels = [b["text"] for row in markup["inline_keyboard"] for b in row]
        self.assertIn("✅ Happy full", labels)
        self.assertEqual(self.ctx.tg.answers[-1][1], "Saved")

    def test_route_toggle_turns_master_on(self):
        self.ctx.settings["depart"]["on"] = False
        router.handle_update(self.ctx, tap("w:mex"))
        self.assertTrue(self.ctx.settings["depart"]["routes"]["mex"]["enabled"])
        self.assertTrue(self.ctx.settings["depart"]["on"])

    def test_oc_quick_set(self):
        router.handle_update(self.ctx, tap("oc:in:12"))
        self.assertEqual(self.ctx.store["oc"]["ready"], T0 + 12 * 3600)
        self.assertEqual(self.ctx.store["oc"]["source"], "manual")
        router.handle_update(self.ctx, tap("oc:clear"))
        self.assertEqual(self.ctx.store["oc"], {})

    def test_ack_stops_buzz(self):
        router.start_demo(self.ctx)
        app.tick(self.ctx)
        self.assertIsNotNone(self.ctx.buzz)
        router.handle_update(self.ctx, tap("ack"))
        self.assertIsNone(self.ctx.buzz)
        self.assertIn("Buzz off", self.ctx.tg.answers[-1][1])

    def test_alert_buttons_open_new_message(self):
        router.handle_update(self.ctx, tap("new:stk:chi"))
        router.handle_update(self.ctx, tap("new:oc"))
        self.assertEqual(self.ctx.tg.edits, [])
        self.assertIn("China", self.ctx.tg.sent[0][1])
        self.assertIn("Organized Crime", self.ctx.tg.sent[1][1])

    def test_legacy_buttons_still_work(self):
        router.handle_update(self.ctx, tap("status"))
        self.assertIn("Hans", self.ctx.tg.edits[-1][1])
        router.handle_update(self.ctx, tap("ack_landing"))   # no crash

    def test_pinned_message_remembers_view(self):
        self.ctx.store["tg"]["pin_id"] = 7
        router.handle_update(self.ctx, tap("nav:set"))
        self.assertEqual(self.ctx.store["tg"]["pin_view"], "set")
        router.handle_update(self.ctx, tap("nav:home"))
        self.assertEqual(self.ctx.store["tg"]["pin_view"], "home")

    def test_strangers_ignored(self):
        router.handle_update(self.ctx, tap("nav:home", chat=999))
        router.handle_update(self.ctx, msg("/menu", chat=999))
        self.assertEqual(self.ctx.tg.edits, [])
        self.assertEqual(self.ctx.tg.sent, [])

    def test_failed_edit_falls_back_to_new_message(self):
        self.ctx.tg.edit_ok = False
        router.handle_update(self.ctx, tap("nav:oc"))
        self.assertIn("Organized Crime", self.ctx.tg.sent[-1][1])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.ctx = make_ctx(clock=Clock(T0))

    def last(self):
        return self.ctx.tg.sent[-1]

    def test_menu(self):
        router.handle_update(self.ctx, msg("/menu"))
        self.assertIs(self.last()[2], K.HOME)

    def test_unknown_shows_menu(self):
        router.handle_update(self.ctx, msg("hi hans"))
        self.assertIn("menu", self.last()[1])
        self.assertIs(self.last()[2], K.HOME)

    def test_stock(self):
        router.handle_update(self.ctx, msg("/stock china"))
        self.assertIn("Panda Plushie", self.last()[1])
        router.handle_update(self.ctx, msg("/stock mexico jaguar"))
        self.assertIn("Jaguar", self.last()[1])
        router.handle_update(self.ctx, msg("/stock atlantis"))
        self.assertIn("Don't know", self.last()[1])

    def test_watch(self):
        router.handle_update(self.ctx, msg("/watch mexico on"))
        self.assertTrue(self.ctx.settings["depart"]["routes"]["mex"]["enabled"])
        router.handle_update(self.ctx, msg("/watch mexico on"))     # idempotent
        self.assertTrue(self.ctx.settings["depart"]["routes"]["mex"]["enabled"])
        router.handle_update(self.ctx, msg("/watch mexico off"))
        self.assertFalse(self.ctx.settings["depart"]["routes"]["mex"]["enabled"])

    def test_oc(self):
        router.handle_update(self.ctx, msg("/oc in 6h"))
        self.assertEqual(self.ctx.store["oc"]["ready"], T0 + 6 * 3600)
        router.handle_update(self.ctx, msg("/oc gibberish"))
        self.assertIn("Couldn't read", self.last()[1])
        router.handle_update(self.ctx, msg("/oc clear"))
        self.assertEqual(self.ctx.store["oc"], {})

    def test_price(self):
        self.ctx.torn.market_payload = {"itemmarket": {
            "item": {"name": "Xanax"}, "listings": [{"price": 830000}]}}
        router.handle_update(self.ctx, msg("/price 206"))
        self.assertIn("830,000", self.ctx.tg.photos[-1][1])
        router.handle_update(self.ctx, msg("/price abc"))
        self.assertIn("item id", self.last()[1])

    def test_preview_and_help(self):
        router.handle_update(self.ctx, msg("/preview"))
        self.assertIn("preview", self.last()[1])
        router.handle_update(self.ctx, msg("/help"))
        self.assertIn("quick guide", self.last()[1])


class ViewTests(unittest.TestCase):
    def test_status_lines(self):
        from hans.core import bars
        snap = bars.parse_snapshot(user_payload(
            cooldowns={"medical": 720}, travel={"time_left": 3600, "destination": "Mexico"},
            status={"state": "Hospital", "until": T0 + 600}))
        txt = V.status(snap, T0, 8, ["⚔️ OC ready 20:00"])
        self.assertIn("16:00", txt)
        self.assertIn("Med 12m", txt)
        self.assertIn("In hospital", txt)
        self.assertIn("Mexico", txt)
        self.assertIn("OC ready", txt)

    def test_stock_status_lines(self):
        p = {"item": "Panda Plushie", "qty": 0, "zero_at": T0 - 300, "restock": T0 + 600}
        self.assertIn("EMPTY", V.stock_status(p, T0, 8))
        self.assertIn("in 10m", V.stock_status(p, T0, 8))
        p2 = {"item": "Panda Plushie", "qty": 812, "empties": T0 + 900}
        self.assertIn("812 in stock", V.stock_status(p2, T0, 8))

    def test_trip_log_view(self):
        trips = [{"land": T0, "code": "chi", "hint": True, "kind": "best_odds", "shelf": "in_stock"},
                 {"land": T0 + 9000, "code": "chi", "hint": False, "kind": "", "shelf": "empty"}]
        txt = V.trip_log(trips, 8)
        self.assertIn("On Hans's ping: <b>1/1</b>", txt)
        self.assertIn("Own timing: <b>0/1</b>", txt)
        self.assertIn("No trips logged", V.trip_log([], 8))

    def test_buzz_countdown_then_landed(self):
        self.assertIn("landing in 42s", V.land_buzz("China", 42.7))
        self.assertIn("LANDED", V.land_buzz("China", -3))


class LoopSimulation(unittest.TestCase):
    """Drive app.run with a fake clock: the fake getUpdates 'blocks' by
    advancing the clock by its timeout, like the real long-poll."""

    def test_full_flight_buzzes_on_time(self):
        clock = Clock(T0)
        torn = FakeTorn(user_payload(travel={"time_left": 1500, "destination": "China"}))
        ctx = make_ctx(torn=torn, clock=clock)
        buzz_times = []
        real_send = ctx.tg.send

        def send(text, markup=None, silent=False):
            if "landing in" in text or "LANDED" in text:
                buzz_times.append(clock.t)
            return real_send(text, markup)
        ctx.tg.send = send

        def get_updates(offset, timeout):
            clock.advance(max(timeout, 0.2))
            left = max(0, int(T0 + 1500 - clock.t))
            torn.payload = user_payload(travel={"time_left": left, "destination": "China"})
            return []
        ctx.tg.get_updates = get_updates
        app.run(ctx, 1500 + 200, sleep=lambda s: clock.advance(s))

        self.assertTrue(buzz_times)
        first = buzz_times[0]
        self.assertTrue(T0 + 1440 <= first <= T0 + 1442, first - T0)   # 60s before
        self.assertTrue(len(buzz_times) >= 50)                         # every ~2s
        self.assertLessEqual(buzz_times[-1], T0 + 1500 + C.BUZZ_AFTER_LANDING)
        self.assertTrue(any("Buzz stopped" in t for t in ctx.tg.texts()))
        self.assertTrue(ctx.store["tg"].get("welcomed"))

    def test_updates_are_answered_and_offset_saved(self):
        clock = Clock(T0)
        ctx = make_ctx(clock=clock)
        ctx.tg.updates = [[msg("/menu", uid=10)], [tap("set:hf:alerts")]]
        orig = ctx.tg.get_updates

        def get_updates(offset, timeout):
            clock.advance(1)
            return orig(offset, timeout)
        ctx.tg.get_updates = get_updates
        app.run(ctx, 10, sleep=lambda s: clock.advance(s))
        from hans.store import Store
        saved = Store(ctx.cfg.state_path).load()
        self.assertEqual(saved["tg"]["offset"], 2)
        self.assertTrue(saved.settings["alerts"]["happy_full"])

    def test_job_errors_never_kill_the_loop(self):
        clock = Clock(T0)
        ctx = make_ctx(clock=clock)

        def boom(selections="x"):
            raise RuntimeError("api down")
        ctx.torn.user = boom

        def get_updates(offset, timeout):
            clock.advance(max(timeout, 1))
            return []
        ctx.tg.get_updates = get_updates
        app.run(ctx, 300, sleep=lambda s: clock.advance(s))   # returns normally


if __name__ == "__main__":
    unittest.main()
