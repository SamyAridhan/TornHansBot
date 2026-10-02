#!/usr/bin/env python3
"""Tests for the daily digest. Run: python3 -m unittest -v test_digest"""

import unittest
from datetime import datetime, timezone

import digest
import torn_watch as tw


class HistoryAnalysisTests(unittest.TestCase):
    def _rows(self, pairs):
        # pairs: list of (iso_timestamp, total)
        return [{"timestamp": ts, "total": str(t)} for ts, t in pairs]

    def test_daily_last_totals_collapses_per_day(self):
        rows = self._rows([
            ("2026-09-30T02:00:00+00:00", 100),
            ("2026-09-30T10:00:00+00:00", 150),   # same local day -> last wins
            ("2026-10-01T10:00:00+00:00", 220),
        ])
        daily = digest.daily_last_totals(rows, tz_offset=0)
        self.assertEqual(daily[0][1], 150)
        self.assertEqual(daily[-1][1], 220)

    def test_current_streak_counts_consecutive_gaining_days(self):
        daily = [("2026-09-29", 100), ("2026-09-30", 150),
                 ("2026-10-01", 160), ("2026-10-02", 200)]
        self.assertEqual(digest.current_streak(daily), 3)

    def test_streak_breaks_on_flat_day(self):
        daily = [("2026-09-30", 150), ("2026-10-01", 150), ("2026-10-02", 200)]
        self.assertEqual(digest.current_streak(daily), 1)

    def test_streak_breaks_on_calendar_gap(self):
        daily = [("2026-09-30", 150), ("2026-10-02", 200)]  # missing 10-01
        self.assertEqual(digest.current_streak(daily), 0)

    def test_gain_last_7_days(self):
        daily = [("2026-09-25", 1000), ("2026-10-02", 1800)]
        self.assertEqual(digest.gain_last_n_days(daily, 7), 800)

    def test_line_of_the_day_stable_and_in_pool(self):
        d = datetime(2026, 10, 2, tzinfo=timezone.utc)
        self.assertEqual(digest.line_of_the_day(d), digest.line_of_the_day(d))
        self.assertIn(digest.line_of_the_day(d), digest.HANS_LINES)


class FormatDigestTests(unittest.TestCase):
    def test_digest_has_key_parts(self):
        now = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)  # 09:00 SGT
        snap = tw.parse_snapshot({
            "energy": {"current": 100, "maximum": 100},
            "nerve": {"current": 25, "maximum": 25},
        })
        stats = {"strength": 1, "defense": 1, "speed": 1, "dexterity": 1, "total": 2000}
        daily = [("2026-09-30", 1500), ("2026-10-01", 1800)]
        msg = digest.format_digest(snap, stats, daily, now, tz_offset=8)
        self.assertIn("Morning", msg)
        self.assertIn("2,000", msg)       # total
        self.assertIn("Energy", msg)
        self.assertIn("<pre>", msg)       # compact bar block


if __name__ == "__main__":
    unittest.main(verbosity=2)
