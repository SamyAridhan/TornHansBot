"""Network clients with injected transports (no real network)."""

import unittest

from hans.net.feeds import PROMBOT_URL, YATA_URL, Feeds
from hans.net.telegram import Telegram
from hans.net.torn import Torn, TornApiError

from .fakes import Clock

SAMPLE = {"stocks": {"chi": {"update": 1, "stocks": [
    {"id": 274, "name": "Panda Plushie", "quantity": 5, "cost": 400}]}}}


class FeedTests(unittest.TestCase):
    def test_fallback_and_cache(self):
        calls = []

        def get(url):
            calls.append(url)
            if url == PROMBOT_URL:
                raise OSError("down")
            return SAMPLE
        clock = Clock(0)
        f = Feeds(get, clock)
        self.assertIn("chi", f.stock())
        self.assertEqual(calls, [PROMBOT_URL, YATA_URL])
        f.stock()
        self.assertEqual(len(calls), 2)            # cached
        clock.advance(61)
        f.stock()
        self.assertEqual(len(calls), 4)            # refreshed

    def test_outage_backs_off(self):
        calls = []

        def get(url):
            calls.append(url)
            raise OSError("down")
        clock = Clock(0)
        f = Feeds(get, clock)
        self.assertEqual(f.stock(), {})
        self.assertEqual(f.stock(), {})
        self.assertEqual(len(calls), 2)            # didn't hammer a dead feed
        self.assertEqual(f.model(), {})


class TornTests(unittest.TestCase):
    def test_error_raises(self):
        t = Torn("k", lambda url: {"error": {"code": 2, "error": "Incorrect key"}})
        with self.assertRaises(TornApiError) as cm:
            t.user()
        self.assertEqual(cm.exception.code, 2)

    def test_url_and_faction_none(self):
        seen = []
        t = Torn("KEY", lambda url: seen.append(url) or {"ok": 1})
        t.user("bars")
        self.assertIn("/user/?selections=bars&key=KEY", seen[0])
        t2 = Torn("k", lambda url: {"error": {"code": 16, "error": "no access"}})
        self.assertIsNone(t2.faction_crimes())


class TelegramTests(unittest.TestCase):
    def test_not_modified_is_ok(self):
        tg = Telegram("t", 1, post=lambda url, p: {
            "ok": False, "description": "Bad Request: message is not modified"})
        self.assertTrue(tg.edit(5, "x")["ok"])

    def test_send_returns_id_and_survives_errors(self):
        tg = Telegram("t", 1, post=lambda url, p: {"ok": True, "result": {"message_id": 9}})
        self.assertEqual(tg.send("hi"), 9)

        def boom(url, p):
            raise OSError("net")
        self.assertIsNone(Telegram("t", 1, post=boom).send("hi"))


if __name__ == "__main__":
    unittest.main()
