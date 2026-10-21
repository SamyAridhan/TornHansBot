"""Bars, cooldowns, status: parse a Torn payload into a snapshot, predict
exact timings from Torn's regen clock, and evaluate edge-triggered alerts."""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------
def parse_snapshot(data: dict) -> dict:
    """Flatten a v1 user payload (bars,cooldowns,travel,profile,battlestats).
    Missing fields default to safe zeros so a partial payload never crashes."""
    data = data or {}

    def bar(name):
        b = data.get(name) or {}
        return {"current": _to_int(b.get("current")), "maximum": _to_int(b.get("maximum"))}

    def regen(name):
        b = data.get(name) or {}
        return {k: _to_int(b.get(k)) for k in ("increment", "interval", "ticktime", "fulltime")}

    cooldowns = data.get("cooldowns") or {}
    travel = data.get("travel") or {}
    status = data.get("status") or {}
    return {
        "energy": bar("energy"), "nerve": bar("nerve"),
        "happy": bar("happy"), "life": bar("life"),
        "regen": {b: regen(b) for b in ("energy", "nerve", "happy", "life")},
        "status": {"state": str(status.get("state") or ""),
                   "until": _to_int(status.get("until"))},
        "battle_total": _to_int(data.get("total")),
        "player_id": _to_int(data.get("player_id")),
        "cooldowns": {k: _to_int(cooldowns.get(k)) for k in ("drug", "medical", "booster")},
        "travel": {"time_left": _to_int(travel.get("time_left")),
                   "destination": travel.get("destination") or "",
                   "departed": _to_int(travel.get("departed")),
                   "arrival": _to_int(travel.get("timestamp")),
                   "method": str(travel.get("method") or "")},
    }


def parse_battlestats(raw: dict) -> dict:
    return {k: _to_int((raw or {}).get(k))
            for k in ("strength", "defense", "speed", "dexterity", "total")}


# --------------------------------------------------------------------------
# Timing math
# --------------------------------------------------------------------------
def seconds_to_value(current: int, target: int, regen: dict) -> Optional[int]:
    """Exact seconds until `current` reaches `target` (0 if there, None if unknown)."""
    if current >= target:
        return 0
    inc, interval = regen.get("increment", 0), regen.get("interval", 0)
    if inc <= 0 or interval <= 0:
        return None
    ticks = math.ceil((target - current) / inc)
    first = regen.get("ticktime", 0) or interval
    return first + (ticks - 1) * interval


def seconds_to_full(current: int, maximum: int, regen: dict) -> Optional[int]:
    if maximum > 0 and current >= maximum:
        return 0
    if regen.get("fulltime", 0) > 0:
        return regen["fulltime"]
    if maximum > 0:
        return seconds_to_value(current, maximum, regen)
    return None


def seconds_to_full_life(current: int, maximum: int) -> Optional[int]:
    """Life has no API clock: ~5% of max every 5 min. Approximate."""
    if maximum <= 0:
        return None
    if current >= maximum:
        return 0
    return math.ceil((maximum - current) / (0.05 * maximum)) * 300


def next_sleep_seconds(snapshot: dict, config: dict, cap: int = 300,
                       min_sleep: int = 60, buffer: int = 8) -> int:
    """Wake just after the soonest mark crossing, within [min_sleep, cap]."""
    waits = [cap]
    reg = snapshot.get("regen", {})
    for bar, mark in (config.get("marks") or {}).items():
        if mark is None:
            continue
        cur = snapshot.get(bar, {}).get("current", 0)
        if cur >= mark:
            continue
        secs = seconds_to_value(cur, mark, reg.get(bar, {}))
        if secs:
            waits.append(secs + buffer)
    return max(min_sleep, min(waits))


# --------------------------------------------------------------------------
# Quiet hours
# --------------------------------------------------------------------------
QUIET_SUPPRESS = {"energy_full", "nerve_full", "happy_full",
                  "energy_mark", "nerve_mark", "energy_overflow"}


def in_quiet_hours(now_utc: datetime, config: dict, tz_offset: int) -> bool:
    q = config.get("quiet_hours", {})
    if not q.get("enabled"):
        return False
    hour = (now_utc + timedelta(hours=tz_offset)).hour
    start, end = q.get("start", 1), q.get("end", 8)
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def filter_quiet(events: list, now_utc: datetime, config: dict, tz_offset: int) -> list:
    if not in_quiet_hours(now_utc, config, tz_offset):
        return events
    return [e for e in events if e["type"] not in QUIET_SUPPRESS]


# --------------------------------------------------------------------------
# Edge-triggered rules
# --------------------------------------------------------------------------
def state_category(state: str) -> str:
    s = (state or "").lower()
    if "hospital" in s:
        return "hospital"
    if "jail" in s or "federal" in s:
        return "jail"
    return "okay" if s else ""


def evaluate(prev: Optional[dict], cur: dict, config: dict) -> list[dict]:
    """Events for transitions between two snapshots. prev=None -> baseline (no events)."""
    if prev is None:
        return []
    events: list[dict] = []
    alerts = config["alerts"]
    reg = cur.get("regen", {})

    for bar in ("energy", "nerve", "happy"):
        if not alerts.get(f"{bar}_full"):
            continue
        cb, pb = cur.get(bar, {}), prev.get(bar, {})
        mx = cb.get("maximum", 0)
        if mx <= 0:
            continue
        now_full = cb.get("current", 0) >= mx
        was_full = pb.get("current", 0) >= pb.get("maximum", 0)
        if now_full and not was_full:
            events.append({"type": f"{bar}_full", "bar": bar,
                           "current": cb["current"], "maximum": mx})

    for bar, mark in (config.get("marks") or {}).items():
        if mark is None:
            continue
        cv = cur.get(bar, {}).get("current", 0)
        pv = prev.get(bar, {}).get("current", 0)
        if cv >= mark > pv:
            mx = cur.get(bar, {}).get("maximum", 0)
            events.append({"type": f"{bar}_mark", "bar": bar, "current": cv,
                           "maximum": mx, "mark": mark,
                           "eta_full": seconds_to_full(cv, mx, reg.get(bar, {}))})

    for cd in ("drug", "medical", "booster"):
        if alerts.get(f"{cd}_cooldown_ended"):
            if prev.get("cooldowns", {}).get(cd, 0) > 0 and \
                    cur.get("cooldowns", {}).get(cd, 0) == 0:
                events.append({"type": f"{cd}_cooldown_ended", "cooldown": cd})

    lead = config.get("overflow_lead_seconds", 600)
    for bar in ("energy", "nerve"):
        if not alerts.get(f"{bar}_overflow"):
            continue
        b = cur.get(bar, {})
        cc, mx = b.get("current", 0), b.get("maximum", 0)
        if mx <= 0 or cc >= mx:
            continue
        cur_eta = seconds_to_full(cc, mx, reg.get(bar, {}))
        pb = prev.get(bar, {})
        prev_eta = seconds_to_full(pb.get("current", 0), pb.get("maximum", 0),
                                   prev.get("regen", {}).get(bar, {}))
        if cur_eta is not None and cur_eta <= lead and (prev_eta is None or prev_eta > lead):
            events.append({"type": f"{bar}_overflow", "bar": bar,
                           "current": cc, "maximum": mx, "eta_full": cur_eta})

    pc = state_category(prev.get("status", {}).get("state", ""))
    cc_ = state_category(cur.get("status", {}).get("state", ""))
    until = cur.get("status", {}).get("until", 0)
    for kind in ("hospital", "jail"):
        if alerts.get(f"{kind}_in") and cc_ == kind and pc not in (kind, ""):
            events.append({"type": f"{kind}_in", "until": until})
        if alerts.get(f"{kind}_out") and pc == kind and cc_ not in (kind, ""):
            events.append({"type": f"{kind}_out"})

    if alerts.get("milestone"):
        pt, ct = prev.get("battle_total", 0), cur.get("battle_total", 0)
        if pt > 0 and ct > pt:
            crossed = [t for t in config.get("milestones", []) if pt < t <= ct]
            if crossed:
                events.append({"type": "milestone", "value": max(crossed), "total": ct})
    return events
