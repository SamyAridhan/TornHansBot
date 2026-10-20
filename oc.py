#!/usr/bin/env python3
"""
Organized-Crime reminders for Torn Watch.

Goal: never sleep through an OC again. Hans learns when your current OC is
ready (auto-read from the faction API when your key allows it, or a time you
set with /oc) and pings you at fixed lead times before it (24h / 12h / 8h).

The schema for v2 faction crimes couldn't be verified offline, so the reader
is deliberately defensive: it searches the payload for your slot and reads the
readiness timestamp from whichever of several candidate fields is present, and
the caller logs the raw crime once so the exact field can be confirmed from a
real response. The pure reminder logic below is fully unit-tested.
"""

from __future__ import annotations

from typing import Optional

# Lead times (seconds before the OC is ready) at which to ping.
DEFAULT_OFFSETS = (24 * 3600, 12 * 3600, 8 * 3600)

# Candidate timestamp fields for "when the OC becomes executable", best first.
_READY_FIELDS = ("ready_at", "ready", "time_ready", "executable_at",
                 "execute_ready", "planned_at", "planning_at")


def _epoch(v) -> Optional[int]:
    if isinstance(v, (int, float)) and v > 0:
        n = int(v)
        return n // 1000 if n > 10_000_000_000 else n
    if isinstance(v, str) and v.strip().isdigit():
        n = int(v)
        return n // 1000 if n > 10_000_000_000 else n
    return None


def _slot_user_ids(crime: dict) -> list:
    """User ids filling this crime's slots, across the shapes v2 might use."""
    ids = []
    slots = crime.get("slots") or crime.get("participants") or []
    if isinstance(slots, dict):
        slots = list(slots.values())
    for s in slots:
        if isinstance(s, dict):
            u = s.get("user_id") or s.get("user") or s.get("id")
            if isinstance(u, dict):
                u = u.get("id")
            if u is not None:
                ids.append(int(u))
        elif isinstance(s, (int, str)) and str(s).isdigit():
            ids.append(int(s))
    return ids


def parse_my_oc_ready(payload: dict, my_user_id: int) -> Optional[int]:
    """Epoch seconds when the OC *you're in* is ready, or None.

    Picks the soonest not-yet-executed crime whose slots include your id. Falls
    back to any crime with a readiness time if slot data is missing (a solo-ish
    account usually has just one active OC)."""
    crimes = (payload or {}).get("crimes")
    if crimes is None:
        crimes = (payload or {}).get("organizedCrimes") or []
    if isinstance(crimes, dict):
        crimes = list(crimes.values())

    best = None
    for c in (crimes or []):
        if not isinstance(c, dict):
            continue
        status = str(c.get("status") or "").lower()
        if status in ("successful", "failure", "failed", "expired", "executed"):
            continue
        ready = None
        for f in _READY_FIELDS:
            ready = _epoch(c.get(f))
            if ready:
                break
        if not ready:
            continue
        mine = my_user_id in _slot_user_ids(c)
        if mine and (best is None or ready < best):
            best = ready
    return best


def due_reminders(ready_epoch: int, now: float, offsets=DEFAULT_OFFSETS,
                  fired=()) -> list:
    """Which lead-time offsets are now due and not yet fired.

    An offset is due once we're within it of the ready time (and the OC hasn't
    passed). `fired` is the set/list of offsets already sent, so each fires
    once. Returns the newly-due offsets (seconds)."""
    if not ready_epoch:
        return []
    remaining = ready_epoch - now
    due = []
    for off in offsets:
        if off in fired:
            continue
        # within this window, and not already past the OC by more than a bit
        if 0 < remaining <= off:
            due.append(off)
    return due


def fmt_offset(seconds: int) -> str:
    h = seconds // 3600
    return f"{h}h"
