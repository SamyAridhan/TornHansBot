"""Organized Crime: find when YOUR OC is ready, and which reminders are due.

The v2 faction-crimes schema couldn't be verified offline, so the reader is
defensive: it accepts several candidate field names and slot shapes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

DEFAULT_OFFSETS = (24 * 3600, 12 * 3600, 8 * 3600)
_READY_FIELDS = ("ready_at", "ready", "time_ready", "executable_at",
                 "execute_ready", "planned_at", "planning_at")
_DONE = ("successful", "failure", "failed", "expired", "executed")


def _epoch(v) -> Optional[int]:
    if isinstance(v, (int, float)) and v > 0:
        n = int(v)
    elif isinstance(v, str) and v.strip().isdigit():
        n = int(v)
    else:
        return None
    return n // 1000 if n > 10_000_000_000 else n


def _slot_user_ids(crime: dict) -> list:
    ids = []
    slots = crime.get("slots") or crime.get("participants") or []
    if isinstance(slots, dict):
        slots = list(slots.values())
    for s in slots:
        if isinstance(s, dict):
            u = s.get("user_id") or s.get("user") or s.get("id")
            if isinstance(u, dict):
                u = u.get("id")
            if u is not None and str(u).isdigit():
                ids.append(int(u))
        elif isinstance(s, (int, str)) and str(s).isdigit():
            ids.append(int(s))
    return ids


def parse_my_oc_ready(payload: dict, my_user_id: int) -> Optional[int]:
    """Epoch when the soonest not-finished OC that includes you is ready."""
    crimes = (payload or {}).get("crimes")
    if crimes is None:
        crimes = (payload or {}).get("organizedCrimes") or []
    if isinstance(crimes, dict):
        crimes = list(crimes.values())
    best = None
    for c in crimes or []:
        if not isinstance(c, dict):
            continue
        if str(c.get("status") or "").lower() in _DONE:
            continue
        ready = None
        for f in _READY_FIELDS:
            ready = _epoch(c.get(f))
            if ready:
                break
        if ready and my_user_id in _slot_user_ids(c) and (best is None or ready < best):
            best = ready
    return best


def due_reminders(ready_epoch: int, now: float, offsets=DEFAULT_OFFSETS, fired=()) -> list:
    """Offsets (seconds before ready) now due and not yet sent."""
    if not ready_epoch:
        return []
    remaining = ready_epoch - now
    return [off for off in offsets if off not in fired and 0 < remaining <= off]


def fmt_offset(seconds: int) -> str:
    return f"{seconds // 3600}h"


def parse_when(arg: str, now: float, tz_offset: int) -> Optional[int]:
    """'in 6h' / 'in 90m' / '6h' / 'DD/MM HH:MM' / 'HH:MM' (local) -> epoch."""
    s = (arg or "").strip().lower()
    if s.startswith("in "):
        s = s[3:].strip()
    try:
        if s.endswith("h") and s[:-1].replace(".", "", 1).isdigit():
            return int(now + float(s[:-1]) * 3600)
        if s.endswith("m") and s[:-1].replace(".", "", 1).isdigit():
            return int(now + float(s[:-1]) * 60)
    except ValueError:
        return None
    local = datetime.fromtimestamp(now, timezone.utc) + timedelta(hours=tz_offset)
    for fmt in ("%d/%m %H:%M", "%d/%m/%y %H:%M", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        when = local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if fmt != "%H:%M":
            when = when.replace(month=t.month, day=t.day)
        if when < local:
            when += timedelta(days=1)
        return int((when - timedelta(hours=tz_offset)).timestamp())
    return None
