#!/usr/bin/env python3
"""Tests for the weekly chart. Run: python3 -m unittest -v test_chart"""

import csv
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

import chart


def _write_history(path, n_days, start_total=100, step=100):
    rows = [["timestamp", "strength", "defense", "speed", "dexterity", "total", "nnb"]]
    t = datetime(2026, 9, 1, tzinfo=timezone.utc)
    tot = start_total
    for i in range(n_days):
        tot += step
        s = tot // 4
        rows.append([(t + timedelta(days=i)).isoformat(timespec="seconds"),
                     s, s, s, tot - 3 * s, tot, 25])
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)


class LoadSeriesTests(unittest.TestCase):
    def test_missing_file(self):
        self.assertEqual(chart.load_series("/nope/nope.csv"), [])

    def test_sorted_and_typed(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "h.csv")
        _write_history(p, 5)
        s = chart.load_series(p)
        self.assertEqual(len(s), 5)
        self.assertTrue(all(isinstance(r["total"], int) for r in s))
        self.assertLess(s[0]["dt"], s[-1]["dt"])

    def test_weekly_gain(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "h.csv")
        _write_history(p, 14, start_total=1000, step=100)  # +100/day
        s = chart.load_series(p)
        self.assertEqual(chart.weekly_gain(s), 700)  # 7 days * 100

    def test_weekly_gain_too_little(self):
        self.assertIsNone(chart.weekly_gain([]))


class RenderTests(unittest.TestCase):
    def test_render_returns_png_bytes(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "h.csv")
        _write_history(p, 10)
        s = chart.load_series(p)
        png = chart.render_png(s)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 1000)


class MultipartTests(unittest.TestCase):
    def test_multipart_contains_parts(self):
        body, boundary = chart._multipart(
            {"chat_id": "123", "caption": "hi"}, "photo", "g.png", b"\x89PNGxx")
        self.assertIn(boundary.encode(), body)
        self.assertIn(b'name="chat_id"', body)
        self.assertIn(b'filename="g.png"', body)
        self.assertIn(b"\x89PNG", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
