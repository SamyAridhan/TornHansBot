#!/usr/bin/env python3
"""Tests for oc.py (OC reminder logic + defensive parse). Run: python3 -m unittest test_oc"""

import unittest
import oc


class ParseReadyTests(unittest.TestCase):
    def test_finds_my_crime_ready_at(self):
        payload = {"crimes": [
            {"id": 1, "status": "successful", "ready_at": 100,
             "slots": [{"user_id": 4538696}]},                      # done -> skip
            {"id": 2, "status": "planning", "ready_at": 5000,
             "slots": [{"user_id": 4538696}, {"user_id": 1}]},      # mine -> use
        ]}
        self.assertEqual(oc.parse_my_oc_ready(payload, 4538696), 5000)

    def test_not_in_any_crime(self):
        payload = {"crimes": [
            {"id": 2, "status": "planning", "ready_at": 5000,
             "slots": [{"user_id": 1}]}]}
        self.assertIsNone(oc.parse_my_oc_ready(payload, 4538696))

    def test_alternate_field_and_slot_shapes(self):
        # user nested as {user:{id}}, timestamp under 'time_ready'
        payload = {"crimes": [
            {"status": "recruiting", "time_ready": 7777,
             "slots": [{"user": {"id": 4538696}}]}]}
        self.assertEqual(oc.parse_my_oc_ready(payload, 4538696), 7777)

    def test_picks_soonest(self):
        payload = {"crimes": [
            {"status": "planning", "ready_at": 9000, "slots": [{"user_id": 42}]},
            {"status": "planning", "ready_at": 6000, "slots": [{"user_id": 42}]},
        ]}
        self.assertEqual(oc.parse_my_oc_ready(payload, 42), 6000)

    def test_empty(self):
        self.assertIsNone(oc.parse_my_oc_ready({}, 42))
        self.assertIsNone(oc.parse_my_oc_ready({"crimes": []}, 42))


class DueRemindersTests(unittest.TestCase):
    def test_fires_within_window_once(self):
        ready = 100000
        offsets = (24 * 3600, 12 * 3600, 8 * 3600)
        # 13h before -> only the 24h window is open
        now = ready - 13 * 3600
        self.assertEqual(oc.due_reminders(ready, now, offsets, fired=[]), [24 * 3600])
        # already fired 24h -> nothing new yet
        self.assertEqual(oc.due_reminders(ready, now, offsets, fired=[24 * 3600]), [])
        # 7h before, 24h already fired -> 12h and 8h now due
        now2 = ready - 7 * 3600
        self.assertEqual(
            oc.due_reminders(ready, now2, offsets, fired=[24 * 3600]),
            [12 * 3600, 8 * 3600])

    def test_none_after_oc_passed(self):
        ready = 100000
        self.assertEqual(oc.due_reminders(ready, ready + 10, fired=[]), [])

    def test_no_ready(self):
        self.assertEqual(oc.due_reminders(None, 123, fired=[]), [])

    def test_fmt(self):
        self.assertEqual(oc.fmt_offset(12 * 3600), "12h")


if __name__ == "__main__":
    unittest.main(verbosity=2)
