#!/usr/bin/env python3
"""Tests for the two-way command bot. Run: python3 -m unittest -v test_bot"""

import unittest
from datetime import datetime, timezone

import bot
import torn_watch as tw


class ParseCommandTests(unittest.TestCase):
    def test_slash_command(self):
        self.assertEqual(bot.parse_command("/status"), ("status", ""))

    def test_bare_word(self):
        self.assertEqual(bot.parse_command("stats"), ("stats", ""))

    def test_command_with_arg(self):
        self.assertEqual(bot.parse_command("/price 206"), ("price", "206"))

    def test_button_label(self):
        self.assertEqual(bot.parse_command("📊 Status"), ("status", ""))
        self.assertEqual(bot.parse_command("⏱ Next full"), ("next", ""))

    def test_group_suffix_stripped(self):
        self.assertEqual(bot.parse_command("/status@HansBot"), ("status", ""))

    def test_start_aliases_help(self):
        self.assertEqual(bot.parse_command("/start"), ("help", ""))

    def test_unknown(self):
        self.assertEqual(bot.parse_command("hello there"), (None, ""))
        self.assertEqual(bot.parse_command(""), (None, ""))


class FormatStatusTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 2, 7, 40, tzinfo=timezone.utc)  # 15:40 SGT
        raw = {
            "energy": {"current": 30, "maximum": 100,
                       "increment": 5, "interval": 900, "ticktime": 300, "fulltime": 12600},
            "nerve": {"current": 25, "maximum": 25,
                      "increment": 1, "interval": 300, "ticktime": 0, "fulltime": 0},
            "happy": {"current": 4200, "maximum": 5000,
                      "increment": 5, "interval": 300, "ticktime": 60, "fulltime": 48000},
            "life": {"current": 175, "maximum": 175},
            "cooldowns": {"drug": 0, "medical": 720, "booster": 0},
            "travel": {"time_left": 0, "destination": ""},
        }
        self.snap = tw.parse_snapshot(raw)

    def test_status_has_all_bars_and_clock(self):
        msg = bot.format_status(self.snap, self.now, tz_offset=8)
        self.assertIn("15:40", msg)       # local clock
        self.assertIn("Energy", msg)
        self.assertIn("Nerve", msg)
        self.assertIn("Happy", msg)
        self.assertIn("3h30m", msg)            # energy eta (compact, no wrap)
        self.assertIn("full", msg)             # nerve already full
        self.assertIn("<pre>", msg)            # aligned monospace table
        self.assertIn("Med 12m", msg)          # only the still-ticking cooldown

    def test_travel_line_only_when_travelling(self):
        self.assertNotIn("transit", bot.format_status(self.snap, self.now))
        raw2 = {"travel": {"time_left": 3600, "destination": "Mexico"}}
        snap2 = tw.parse_snapshot(raw2)
        self.assertIn("Mexico", bot.format_status(snap2, self.now))


class FormatNextTests(unittest.TestCase):
    def test_eta_and_local_clock(self):
        now = datetime(2026, 10, 2, 7, 40, tzinfo=timezone.utc)
        snap = tw.parse_snapshot({
            "energy": {"current": 30, "maximum": 100,
                       "increment": 5, "interval": 900, "ticktime": 300, "fulltime": 12600},
            "nerve": {"current": 25, "maximum": 25},
        })
        msg = bot.format_next(snap, now, tz_offset=8)
        self.assertIn("3h30m", msg)
        self.assertIn("19:10", msg)        # 15:40 + 3h30m
        self.assertIn("already full", msg)  # nerve


class FormatStatsTests(unittest.TestCase):
    def test_stats_and_gain(self):
        stats = {"strength": 1000, "defense": 2000, "speed": 1500,
                 "dexterity": 500, "total": 5000}
        prev = {"timestamp": "2026-10-01T09:00:00", "total": "4200"}
        msg = bot.format_stats(stats, prev)
        self.assertIn("5,000", msg)
        self.assertIn("+800", msg)
        self.assertIn("2026-10-01 09:00", msg)

    def test_stats_without_history(self):
        stats = {"strength": 1, "defense": 1, "speed": 1, "dexterity": 1, "total": 4}
        msg = bot.format_stats(stats, None)
        self.assertIn("TOT", msg)
        self.assertNotIn("since", msg)


class FormatPriceTests(unittest.TestCase):
    def test_with_price_and_name(self):
        msg = bot.format_price("206", "Xanax", 820000)
        self.assertIn("Xanax", msg)
        self.assertIn("820,000", msg)

    def test_no_price(self):
        msg = bot.format_price("206", None, None)
        self.assertIn("Couldn't", msg)
        self.assertIn("206", msg)


class OffsetTests(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile, os
        d = tempfile.mkdtemp()
        p = os.path.join(d, "off.json")
        self.assertIsNone(bot.load_offset(p))
        bot.save_offset(42, p)
        self.assertEqual(bot.load_offset(p), 42)


class PinStateTests(unittest.TestCase):
    def test_roundtrip(self):
        import tempfile, os
        p = os.path.join(tempfile.mkdtemp(), "pin.json")
        self.assertEqual(bot._load_pin(p), {})
        bot._save_pin({"message_id": 7, "last_text": "hi"}, p)
        self.assertEqual(bot._load_pin(p)["message_id"], 7)


class HandleTests(unittest.TestCase):
    def test_help_needs_no_network(self):
        msg = bot.handle("help", "", api_key="", now_utc=datetime.now(timezone.utc))
        self.assertIn("/status", msg)

    def test_price_bad_arg_no_network(self):
        msg = bot.handle("price", "notanumber", api_key="",
                         now_utc=datetime.now(timezone.utc))
        self.assertIn("item id", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
