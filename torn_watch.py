#!/usr/bin/env python3
"""
Torn Watch — a read-only watcher for your own Torn account.

What it does:
  Polls the OFFICIAL Torn API for your bars (energy/nerve/happy/life),
  cooldowns (drug/medical/booster) and travel status, and sends YOU a
  Telegram message when something worth acting on happens (energy full,
  nerve full, a cooldown ends, you land from travel).

What it deliberately does NOT do:
  It never performs any game action. It reads and it notifies. You click.
  This keeps it fully within Torn's rules (automated *gameplay* is banned;
  reading your own data via the official API is not).

Design notes:
  - Zero external dependencies (standard library only) so it runs anywhere.
  - Network calls (Torn fetch, Telegram send) are injectable, so the logic
    is fully unit-testable without touching the network.
  - Alerts are EDGE-TRIGGERED: you get one message when a bar becomes full,
    not one every poll while it stays full. State is persisted in a JSON file.
  - First run establishes a baseline silently (no alert burst).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import random
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

TORN_API_BASE = "https://api.torn.com"
TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_SELECTIONS = "bars,cooldowns,travel,profile,battlestats"
DEFAULT_STATE_FILE = "state.json"

# Type aliases for the injectable I/O functions
HttpGet = Callable[[str], dict]
Notifier = Callable[[str], None]


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
def default_config() -> dict:
    """Which alerts are on, and any custom thresholds.

    A threshold of None for a bar means 'alert when current >= maximum'.
    Set an integer to alert at a custom level (e.g. nerve 24 so you act
    before it overflows/wastes regen).
    """
    return {
        "alerts": {
            "energy_full": True,
            "nerve_full": True,
            "happy_full": False,   # off by default; happy fills fast and is noisy
            "drug_cooldown_ended": True,
            "medical_cooldown_ended": False,
            "booster_cooldown_ended": False,
            "travel_landed": True,
            # Smarter alerts (Phase 2):
            "energy_overflow": True,   # warn before energy caps & wastes regen
            "hospital_out": True,      # you've left hospital
            "jail_out": True,          # you've left jail
            "milestone": True,         # total battle stats crossed a round number
            # Test/verify mode: ping on ANY increase of energy/nerve.
            # Noisy (roughly one ping per poll while not capped) — meant for
            # confirming the pipeline works, not permanent use. Toggled by the
            # ALERT_ON_INCREASE env var (see apply_env_overrides / main).
            "energy_increase": False,
            "nerve_increase": False,
        },
        "thresholds": {
            "energy": None,   # None -> maximum
            "nerve": None,    # None -> maximum
            "happy": None,    # None -> maximum
        },
        # "Marks": ping each time a bar rises to/above these levels (edge-
        # triggered, so it fires on the way up and re-arms after you spend
        # back below). Independent of the *_full alerts. Set to None to disable.
        "marks": {
            "energy": 25,
            "nerve": 10,
        },
        # How early (seconds) to warn that energy is about to cap. 600 = 10 min,
        # i.e. two 5-energy ticks of headroom to go spend it.
        "overflow_lead_seconds": 600,
        # Total-battle-stat levels worth a cheer. Tuned to fire often early on
        # and then at the big round numbers later.
        "milestones": [
            5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000,
            1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000,
            50_000_000, 100_000_000,
        ],
        # Overnight muting: between these LOCAL hours the noisy regen pings
        # (energy/nerve full, marks, overflow, …) are held back so you can
        # sleep. Rare/important ones (milestone, hospital-out, jail-out,
        # travel-landed, cooldowns) still come through.
        "quiet_hours": {"enabled": True, "start": 1, "end": 8},
    }


# Alerts suppressed during quiet hours (the routine, re-firing ones).
QUIET_SUPPRESS = {
    "energy_full", "nerve_full", "happy_full",
    "energy_mark", "nerve_mark",
    "energy_increase", "nerve_increase",
    "energy_overflow",
}


def in_quiet_hours(now_utc: datetime, config: dict, tz_offset: int) -> bool:
    q = config.get("quiet_hours", {})
    if not q.get("enabled"):
        return False
    hour = (now_utc + timedelta(hours=tz_offset)).hour
    start, end = q.get("start", 1), q.get("end", 8)
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end   # window wraps past midnight


def filter_quiet(events: list[dict], now_utc: datetime, config: dict,
                 tz_offset: int) -> list[dict]:
    """Drop the noisy alert types during quiet hours; keep the important ones."""
    if not in_quiet_hours(now_utc, config, tz_offset):
        return events
    return [e for e in events if e["type"] not in QUIET_SUPPRESS]


def apply_env_overrides(config: dict) -> dict:
    """Adjust config from environment variables (for GitHub Actions / cron).

    ALERT_ON_INCREASE=1  -> test/verify mode: ping on any energy/nerve increase,
                            and turn the (redundant, in this mode) *_full pings
                            off to avoid double messages. Unset -> normal mode.
    """
    flag = os.environ.get("ALERT_ON_INCREASE", "").strip().lower()
    if flag in ("1", "true", "yes", "on"):
        config["alerts"]["energy_increase"] = True
        config["alerts"]["nerve_increase"] = True
        config["alerts"]["energy_full"] = False
        config["alerts"]["nerve_full"] = False
    # Quiet-hours overrides (all optional)
    qe = os.environ.get("QUIET_ENABLED", "").strip().lower()
    if qe in ("0", "false", "no", "off"):
        config["quiet_hours"]["enabled"] = False
    elif qe in ("1", "true", "yes", "on"):
        config["quiet_hours"]["enabled"] = True
    for key, env in (("start", "QUIET_START"), ("end", "QUIET_END")):
        v = os.environ.get(env, "").strip()
        if v.isdigit():
            config["quiet_hours"][key] = int(v)
    return config


# --------------------------------------------------------------------------
# Parsing — turn a raw Torn API payload into a flat snapshot
# --------------------------------------------------------------------------
def parse_snapshot(data: dict) -> dict:
    """Extract the fields we care about, defensively.

    Missing fields default to safe values so a partial payload never crashes
    the evaluator. Field names follow the Torn API v1 'bars', 'cooldowns' and
    'travel' selections. The first live run confirms these against your key.
    """
    def bar(name: str) -> dict:
        b = data.get(name) or {}
        return {
            "current": _to_int(b.get("current")),
            "maximum": _to_int(b.get("maximum")),
        }

    def regen(name: str) -> dict:
        # Torn's bars selection also returns the regen clock for each bar:
        #   increment  - points gained per tick (energy 5, nerve 1, ...)
        #   interval   - seconds between ticks (energy 900, nerve 300, ...)
        #   ticktime   - seconds until the NEXT tick
        #   fulltime   - seconds until the bar is full (0 if already full)
        # We keep these separate from current/maximum so the existing snapshot
        # shape (and its tests) is untouched, and use them to predict exact
        # timing in messages.
        b = data.get(name) or {}
        return {
            "increment": _to_int(b.get("increment")),
            "interval": _to_int(b.get("interval")),
            "ticktime": _to_int(b.get("ticktime")),
            "fulltime": _to_int(b.get("fulltime")),
        }

    cooldowns = data.get("cooldowns") or {}
    travel = data.get("travel") or {}
    status = data.get("status") or {}   # from the 'profile' selection

    return {
        "energy": bar("energy"),
        "nerve": bar("nerve"),
        "happy": bar("happy"),
        "life": bar("life"),
        "regen": {
            "energy": regen("energy"),
            "nerve": regen("nerve"),
            "happy": regen("happy"),
            "life": regen("life"),
        },
        # player state ("Okay"/"Hospital"/"Jail"/"Federal"/"Traveling"/…) and
        # the unix time it lasts until; used for hospital/jail-out alerts.
        "status": {
            "state": str(status.get("state") or ""),
            "until": _to_int(status.get("until")),
        },
        # total battle stats (from the 'battlestats' selection) for milestones
        "battle_total": _to_int(data.get("total")),
        "cooldowns": {
            "drug": _to_int(cooldowns.get("drug")),
            "medical": _to_int(cooldowns.get("medical")),
            "booster": _to_int(cooldowns.get("booster")),
        },
        "travel": {
            # time_left is seconds until you land; 0 = not travelling / landed
            "time_left": _to_int(travel.get("time_left")),
            "destination": travel.get("destination") or "",
        },
    }


def _to_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# Timing math — use Torn's regen clock to predict EXACTLY when a bar reaches
# a value, instead of guessing from how often we happen to poll.
# --------------------------------------------------------------------------
def seconds_to_value(current: int, target: int, regen: dict) -> Optional[int]:
    """Seconds until `current` climbs to `target`, from Torn's regen fields.

    Returns 0 if already at/above target, None if we can't tell (no regen
    data yet). The bar gains `increment` points every `interval` seconds; the
    next tick lands in `ticktime` seconds, so the maths is exact, not fuzzy.
    """
    if current >= target:
        return 0
    increment = regen.get("increment", 0)
    interval = regen.get("interval", 0)
    if increment <= 0 or interval <= 0:
        return None
    ticktime = regen.get("ticktime", 0)
    need = target - current
    ticks = math.ceil(need / increment)
    # first tick arrives in ticktime (fall back to a full interval if unknown)
    first = ticktime if ticktime > 0 else interval
    return first + (ticks - 1) * interval


def seconds_to_full(current: int, maximum: int, regen: dict) -> Optional[int]:
    """Seconds until the bar is full. Prefers Torn's own `fulltime` field
    (authoritative) and falls back to computing it from the regen clock."""
    if maximum > 0 and current >= maximum:
        return 0
    fulltime = regen.get("fulltime", 0)
    if fulltime > 0:
        return fulltime
    if maximum > 0:
        return seconds_to_value(current, maximum, regen)
    return None


# Adaptive polling: how long to sleep before the next check. Instead of a fixed
# interval, we wake exactly when a bar is due to reach its mark (from Torn's own
# regen clock), per bar, whichever is sooner — with a safety-net cap so the other
# alerts (cooldowns, travel, overflow, full, hospital/jail) still get checked.
POLL_CAP_SECONDS = 300      # never wait longer than this (safety net)
POLL_MIN_SECONDS = 60       # never hammer faster than this
POLL_BUFFER_SECONDS = 8     # wake a few sec AFTER the tick, so the mark is met


def next_sleep_seconds(snapshot: dict, config: dict,
                       cap: int = POLL_CAP_SECONDS,
                       min_sleep: int = POLL_MIN_SECONDS,
                       buffer: int = POLL_BUFFER_SECONDS) -> int:
    """Seconds to sleep before the next poll.

    = the soonest upcoming mark-crossing (energy->25, nerve->10, …) computed
    from the regen clock, capped by `cap` and floored by `min_sleep`. Bars
    already at/above their mark don't schedule anything (nothing to wait for).
    """
    waits = [cap]
    reg = snapshot.get("regen", {})
    for bar, mark in (config.get("marks") or {}).items():
        if mark is None:
            continue
        cur = snapshot.get(bar, {}).get("current", 0)
        if cur >= mark:
            continue  # already there — no crossing to wake for
        secs = seconds_to_value(cur, mark, reg.get(bar, {}))
        if secs is not None and secs > 0:
            waits.append(secs + buffer)
    return max(min_sleep, min(waits))


def fmt_duration(seconds: Optional[int]) -> str:
    """Human-friendly compact duration: '42m', '1h 05m', '2h', '<1m'."""
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return "<1m"
    mins = seconds // 60
    if mins < 60:
        return f"{mins}m"
    hours, rem = divmod(mins, 60)
    if rem == 0:
        return f"{hours}h"
    return f"{hours}h {rem:02d}m"


def fmt_duration_compact(seconds: Optional[int]) -> str:
    """Tight duration with no spaces, for aligned tables: '4h10m', '35m', 'now'."""
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return "<1m"
    mins = seconds // 60
    if mins < 60:
        return f"{mins}m"
    hours, rem = divmod(mins, 60)
    return f"{hours}h" if rem == 0 else f"{hours}h{rem:02d}m"


def progress_bar(current: int, maximum: int, width: int = 5) -> str:
    """A tiny block-character gauge, e.g. ▰▰▰▱▱."""
    if maximum <= 0:
        return "▱" * width
    frac = max(0.0, min(1.0, current / maximum))
    filled = int(round(frac * width))
    return "▰" * filled + "▱" * (width - filled)


def html_escape(s: str) -> str:
    """Escape the three characters that matter for Telegram HTML parse_mode."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# --------------------------------------------------------------------------
# Core rule evaluation — PURE and edge-triggered
# --------------------------------------------------------------------------
def evaluate(prev: Optional[dict], cur: dict, config: dict) -> tuple[list[dict], dict]:
    """Compare the previous snapshot to the current one, return (events, new_state).

    Edge-triggered: an event fires only on the transition into the condition,
    so a bar that stays full doesn't re-alert. `prev is None` (first run)
    establishes a baseline with no events.

    Returns the *current snapshot* as the new state to persist.
    """
    if prev is None:
        return [], cur

    events: list[dict] = []
    alerts = config["alerts"]
    thresholds = config["thresholds"]

    # --- Bars becoming full (or hitting a custom threshold) ---
    for bar in ("energy", "nerve", "happy"):
        if not alerts.get(f"{bar}_full"):
            continue
        cur_bar = cur.get(bar, {})
        prev_bar = prev.get(bar, {})
        threshold = thresholds.get(bar)
        cur_target = threshold if threshold is not None else cur_bar.get("maximum", 0)
        prev_target = threshold if threshold is not None else prev_bar.get("maximum", 0)
        if cur_target <= 0:
            continue  # no known maximum yet, skip
        now_full = cur_bar.get("current", 0) >= cur_target
        was_full = prev_bar.get("current", 0) >= prev_target
        if now_full and not was_full:
            cur_reg = cur.get("regen", {}).get(bar, {})
            events.append({
                "type": f"{bar}_full",
                "bar": bar,
                "current": cur_bar.get("current", 0),
                "maximum": cur_bar.get("maximum", 0),
                "threshold": cur_target,
                # time until the bar is actually capped (0 if threshold == max)
                "eta_full": seconds_to_full(
                    cur_bar.get("current", 0), cur_bar.get("maximum", 0), cur_reg),
            })

    # --- Bar "marks": crossing up to/over a set level (edge-triggered) ---
    marks = config.get("marks", {})
    for bar, mark in marks.items():
        if mark is None:
            continue
        cur_v = cur.get(bar, {}).get("current", 0)
        prev_v = prev.get(bar, {}).get("current", 0)
        if cur_v >= mark and prev_v < mark:
            cur_bar = cur.get(bar, {})
            cur_reg = cur.get("regen", {}).get(bar, {})
            events.append({
                "type": f"{bar}_mark",
                "bar": bar,
                "current": cur_v,
                "maximum": cur_bar.get("maximum", 0),
                "mark": mark,
                # exact seconds until this bar is full, from Torn's regen clock
                "eta_full": seconds_to_full(
                    cur_v, cur_bar.get("maximum", 0), cur_reg),
            })

    # --- Bars increasing at all (test/verify mode — noisy) ---
    for bar in ("energy", "nerve"):
        if not alerts.get(f"{bar}_increase"):
            continue
        cur_v = cur.get(bar, {}).get("current", 0)
        prev_v = prev.get(bar, {}).get("current", 0)
        if cur_v > prev_v:
            events.append({
                "type": f"{bar}_increase",
                "bar": bar,
                "current": cur_v,
                "delta": cur_v - prev_v,
                "maximum": cur.get(bar, {}).get("maximum", 0),
            })

    # --- Cooldowns ending (transition from >0 to 0) ---
    for cd in ("drug", "medical", "booster"):
        if not alerts.get(f"{cd}_cooldown_ended"):
            continue
        prev_cd = prev.get("cooldowns", {}).get(cd, 0)
        cur_cd = cur.get("cooldowns", {}).get(cd, 0)
        if prev_cd > 0 and cur_cd == 0:
            events.append({"type": f"{cd}_cooldown_ended", "cooldown": cd})

    # --- Travel landed (time_left transition from >0 to 0) ---
    if alerts.get("travel_landed"):
        prev_tl = prev.get("travel", {}).get("time_left", 0)
        cur_tl = cur.get("travel", {}).get("time_left", 0)
        if prev_tl > 0 and cur_tl == 0:
            events.append({
                "type": "travel_landed",
                "destination": cur.get("travel", {}).get("destination", ""),
            })

    # --- Energy about to overflow (enters the final `lead` window) ---
    if alerts.get("energy_overflow"):
        lead = config.get("overflow_lead_seconds", 600)
        eb = cur.get("energy", {})
        cc, mx = eb.get("current", 0), eb.get("maximum", 0)
        if mx > 0 and cc < mx:
            cur_eta = seconds_to_full(cc, mx, cur.get("regen", {}).get("energy", {}))
            pe = prev.get("energy", {})
            prev_eta = seconds_to_full(
                pe.get("current", 0), pe.get("maximum", 0),
                prev.get("regen", {}).get("energy", {}))
            # fire once as it crosses INTO the window; re-arms after you spend
            entering = cur_eta is not None and cur_eta <= lead and \
                (prev_eta is None or prev_eta > lead)
            if entering:
                events.append({
                    "type": "energy_overflow", "bar": "energy",
                    "current": cc, "maximum": mx, "eta_full": cur_eta,
                })

    # --- Out of hospital / jail (state transition back to Okay) ---
    prev_cat = _state_category(prev.get("status", {}).get("state", ""))
    cur_cat = _state_category(cur.get("status", {}).get("state", ""))
    if alerts.get("hospital_out") and prev_cat == "hospital" and cur_cat not in ("hospital", ""):
        events.append({"type": "hospital_out"})
    if alerts.get("jail_out") and prev_cat == "jail" and cur_cat not in ("jail", ""):
        events.append({"type": "jail_out"})

    # --- Battle-stat milestone crossed ---
    if alerts.get("milestone"):
        pt = prev.get("battle_total", 0)
        ct = cur.get("battle_total", 0)
        # pt must be a real prior reading (>0), else a fresh deploy would fire
        # a burst for every milestone below your current total.
        if pt > 0 and ct > pt:
            crossed = [t for t in config.get("milestones", []) if pt < t <= ct]
            if crossed:
                events.append({
                    "type": "milestone", "value": max(crossed), "total": ct,
                })

    return events, cur


def _state_category(state: str) -> str:
    """Bucket Torn's status.state string into hospital / jail / okay / ''.

    Defensive substring match so minor wording differences ('In Hospital' vs
    'Hospital', 'Federal') don't break it, and an empty/unknown state yields
    '' (which never triggers a transition alert)."""
    s = (state or "").lower()
    if "hospital" in s:
        return "hospital"
    if "jail" in s or "federal" in s:
        return "jail"
    if s:
        return "okay"
    return ""


# --------------------------------------------------------------------------
# Message formatting — "Hans" has a laconic underworld-fixer voice. Each alert
# is ONE rich line, rendered with Telegram's HTML parse_mode:
#
#     ⚡ <b>Energy's maxed — hit the gym before it spills</b>
#        · <code>100/100</code> ▰▰▰▰▰
#
# A random flavor line keeps alerts from reading identically; the stat tail
# (value, mini-gauge, and — for "mark" alerts — the exact "full in …" from
# Torn's regen clock) is appended uniformly. Every energy line still contains
# "Energy", every nerve line "Nerve", etc. (keeps them greppable + testable).
# --------------------------------------------------------------------------
_EMOJI = {
    "energy_full": "⚡", "energy_mark": "⚡", "energy_increase": "⚡",
    "nerve_full": "🔴", "nerve_mark": "🔴", "nerve_increase": "🔴",
    "happy_full": "🙂",
    "energy_overflow": "⏳",
    "drug_cooldown_ended": "💊",
    "medical_cooldown_ended": "🩹",
    "booster_cooldown_ended": "🧪",
    "travel_landed": "✈️",
    "hospital_out": "🏥",
    "jail_out": "🔓",
    "milestone": "🏆",
}

_FLAVOR = {
    "energy_full": [
        "Energy's maxed — hit the gym before it spills",
        "Full tank of Energy. Go move some iron, champ",
        "Energy topped out and nowhere to spend it. That's waste — train",
        "Energy's at the brim. The weights are calling",
        "Capped on Energy. Any more and it just evaporates",
    ],
    "nerve_full": [
        "Nerve's full — this city won't rob itself",
        "Full Nerve. Go make some poor life choices",
        "Nerve maxed out. Time to earn your reputation",
        "Nerve's brimming — somewhere, a crime is waiting",
        "All the Nerve you can hold. Put it to work",
    ],
    "happy_full": [
        "Happy's maxed — prime time to train hard",
        "Happy is full. Your gains will thank you",
        "Peak Happy. Hit the gym while it lasts",
    ],
    "energy_mark": [
        "Energy's over the line — enough to get moving",
        "Energy's stacked up. Time to spend some",
        "Enough Energy in the tank to do real work",
    ],
    "nerve_mark": [
        "Nerve's over the line — enough for a job or two",
        "Nerve banked. Go put it to work",
        "Enough Nerve for some mischief",
    ],
    "energy_increase": [
        "Energy ticking up",
        "Energy on the rise",
    ],
    "nerve_increase": [
        "Nerve creeping up",
        "Nerve on the rise",
    ],
    "energy_overflow": [
        "Energy's about to cap — spend it before it spills",
        "Energy nearly full. Burn some before regen goes to waste",
        "Energy's topping off soon — don't leave gains on the table",
    ],
    "hospital_out": [
        "You're out of hospital. Back on your feet",
        "Discharged — out of hospital and good to go",
        "Hospital's done with you. Get back to it",
    ],
    "jail_out": [
        "You're out of jail. Lay low for a bit",
        "Sprung from jail — you're a free agent again",
        "Jail's behind you. Try not to make it a habit",
    ],
    "milestone": [
        "Milestone — you cracked {value} total stats! Now at {total}",
        "{value} total stats and climbing. Sitting at {total} now",
        "Big one: {value} total cleared. You're at {total}",
    ],
    "drug_cooldown_ended": [
        "Drug cooldown's up — you're clear to dose again",
        "Drug cooldown cleared. The pharmacy's open, so to speak",
        "Off drug cooldown. Re-dose when you're ready",
    ],
    "medical_cooldown_ended": [
        "Medical cooldown's up — patch kit's ready when you are",
        "Off medical cooldown. The doc will see you now",
    ],
    "booster_cooldown_ended": [
        "Booster cooldown cleared. Stock up",
        "Off booster cooldown — top yourself up",
    ],
    "travel_landed": [
        "Touched down in {dest}. Try to stay out of the papers",
        "Landed in {dest} — business awaits",
        "Arrived in {dest}. Watch your back",
    ],
}


def format_event(ev: dict) -> str:
    """Render one event as a single HTML line for Telegram."""
    t = ev["type"]
    pool = _FLAVOR.get(t)
    emoji = _EMOJI.get(t, "•")
    if not pool:
        return f"• {html_escape(t)}"

    dest = html_escape(ev.get("destination") or "your destination")
    flavor = random.choice(pool).format(
        dest=dest,
        value=f"{ev.get('value', 0):,}",
        total=f"{ev.get('total', 0):,}",
    )
    head = f"{emoji} <b>{flavor}</b>"

    # Bar events (energy/nerve/happy and variants) get a stat tail + mini-gauge.
    cur = ev.get("current")
    if ev.get("bar") and isinstance(cur, int):
        mx = ev.get("maximum") or 0
        gauge = progress_bar(cur, mx)
        stat = f"<code>{cur}/{mx}</code>" if mx else f"<code>{cur}</code>"
        tail = f"{stat} {gauge}"
        delta = ev.get("delta")
        if t.endswith("_increase") and isinstance(delta, int):
            tail += f" <i>(+{delta})</i>"
        eta = ev.get("eta_full")
        if t.endswith("_mark") and eta:
            tail += f" · full in <b>{fmt_duration(eta)}</b>"
        elif t == "energy_overflow" and eta:
            tail += f" · caps in <b>{fmt_duration(eta)}</b>"
        return f"{head} · {tail}"

    return head


def format_message(events: list[dict]) -> str:
    """One Telegram message covering all events from this poll (HTML parse_mode)."""
    lines = [format_event(e) for e in events]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Real network I/O (injectable so tests never hit the network)
# --------------------------------------------------------------------------
def make_torn_fetcher(api_key: str, selections: str = DEFAULT_SELECTIONS) -> HttpGet:
    def fetch(_ignored: str = "") -> dict:
        params = urllib.parse.urlencode({"selections": selections, "key": api_key})
        url = f"{TORN_API_BASE}/user/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "torn-watch/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            raise TornApiError(err.get("code"), err.get("error"))
        return data
    return fetch


def make_market_fetcher(api_key: str, item_id: str) -> HttpGet:
    """Fetch one item's market listings via Torn API v2.

    The old v1 'itemmarket' selection was retired ("only available in API v2"),
    so we hit GET /v2/market/{id}/itemmarket. Listings still carry price/amount;
    tier2.parse_market handles the shape defensively."""
    def fetch(_ignored: str = "") -> dict:
        params = urllib.parse.urlencode({"key": api_key})
        url = f"{TORN_API_BASE}/v2/market/{item_id}/itemmarket?{params}"
        req = urllib.request.Request(
            url, headers={"User-Agent": "torn-watch/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if isinstance(data, dict) and "error" in data:
            err = data["error"] or {}
            raise TornApiError(err.get("code"), err.get("error") or err.get("message"))
        return data
    return fetch


def make_telegram_notifier(bot_token: str, chat_id: str) -> Notifier:
    def send(text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "torn-watch/1.0"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    return send


class TornApiError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"Torn API error {code}: {message}")


# --------------------------------------------------------------------------
# State persistence
# --------------------------------------------------------------------------
def load_state(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f)


# --------------------------------------------------------------------------
# Orchestration — one poll cycle
# --------------------------------------------------------------------------
def run_once(config: dict, fetcher: HttpGet, notifier: Notifier,
             prev_state: Optional[dict],
             notify_filter: Optional[Callable[[list], list]] = None) -> tuple[list[dict], dict]:
    """Fetch, evaluate, notify. Returns (all_events, new_state).

    `notify_filter` (optional) decides which events actually get sent — used
    for quiet hours. The full event list is still returned, and state is still
    advanced, so a suppressed alert is simply not delivered (never re-queued).
    """
    raw = fetcher("")
    snapshot = parse_snapshot(raw)
    events, new_state = evaluate(prev_state, snapshot, config)
    to_send = notify_filter(events) if notify_filter else events
    if to_send:
        notifier(format_message(to_send))
    return events, new_state


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------
def _mock_payload() -> dict:
    """A realistic sample payload for --dry-run (no key needed).

    Bars sit just past their marks (25 energy / 10 nerve) and carry regen
    fields, so the dry-run shows the 'mark' alerts with an exact 'full in …'.
    """
    return {
        "energy": {"current": 30, "maximum": 100,
                   "increment": 5, "interval": 900, "ticktime": 300, "fulltime": 12600},
        "nerve": {"current": 12, "maximum": 25,
                  "increment": 1, "interval": 300, "ticktime": 120, "fulltime": 3720},
        "happy": {"current": 4000, "maximum": 5000,
                  "increment": 5, "interval": 300, "ticktime": 60, "fulltime": 59760},
        "life": {"current": 175, "maximum": 175},
        "cooldowns": {"drug": 0, "medical": 0, "booster": 0},
        "travel": {"time_left": 0, "destination": "Switzerland"},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Torn Watch — read-only account watcher.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Use a mock payload and print alerts instead of sending.")
    parser.add_argument("--check", action="store_true",
                        help="Fetch live data with TORN_API_KEY (no Telegram needed) and "
                             "print the raw payload next to the parsed snapshot, to confirm "
                             "the API field names before relying on alerts.")
    parser.add_argument("--log-stats", metavar="PATH", default=None,
                        help="Fetch battle stats with TORN_API_KEY, append a row to the "
                             "CSV at PATH (with a timestamp), then exit. No Telegram needed.")
    parser.add_argument("--check-market", metavar="ITEM_ID", default=None,
                        help="Fetch one item's market listing and print raw + parsed lowest "
                             "price, to confirm the market API shape. No Telegram needed.")
    parser.add_argument("--watch-market", action="store_true",
                        help="Read watchlist.json, alert on items at/under their target price. "
                             "Uses market_state.json for dedup.")
    parser.add_argument("--loop", type=int, metavar="SECONDS", default=None,
                        help="Run continuously for ~SECONDS, sleeping adaptively between "
                             "polls so it wakes exactly when energy/nerve are due to hit "
                             "their marks (from Torn's regen clock), capped for other alerts.")
    parser.add_argument("--state", default=os.environ.get("STATE_FILE", DEFAULT_STATE_FILE),
                        help="Path to the state file (default: state.json).")
    args = parser.parse_args(argv)

    config = default_config()
    apply_env_overrides(config)

    if args.log_stats:
        import tier2
        from datetime import datetime, timezone
        api_key = os.environ.get("TORN_API_KEY")
        if not api_key:
            print("Missing env var: TORN_API_KEY", file=sys.stderr)
            return 2
        try:
            raw = make_torn_fetcher(api_key, "battlestats,bars")("")
        except TornApiError as e:
            print(f"Torn API error: {e}", file=sys.stderr)
            return 3
        stats = tier2.parse_battlestats(raw)
        nerve_max = _to_int((raw.get("nerve") or {}).get("maximum"))
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        tier2.append_history(args.log_stats, ts, stats, nerve_max)
        print(f"[stats] {ts} total={stats['total']} "
              f"(str {stats['strength']}, def {stats['defense']}, "
              f"spd {stats['speed']}, dex {stats['dexterity']}) "
              f"nerve_bar={nerve_max} -> {args.log_stats}")
        return 0

    if args.check_market:
        import tier2
        api_key = os.environ.get("TORN_API_KEY")
        if not api_key:
            print("Missing env var: TORN_API_KEY", file=sys.stderr)
            return 2
        try:
            raw = make_market_fetcher(api_key, args.check_market)("")
        except TornApiError as e:
            print(f"Torn API error: {e}", file=sys.stderr)
            return 3
        print("=== RAW market payload ===")
        print(json.dumps(raw, indent=2)[:2000])
        print("\n=== PARSED lowest price ===")
        print(tier2.parse_market(raw))
        print("\nIf the lowest price is None but the raw payload clearly has "
              "listings, the listing field name needs adjusting in tier2.parse_market().")
        return 0

    if args.watch_market:
        import tier2
        api_key = os.environ.get("TORN_API_KEY")
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if not (api_key and bot_token and chat_id):
            print("Missing env vars for --watch-market (need TORN_API_KEY, "
                  "TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)", file=sys.stderr)
            return 2
        watchlist = {}
        if os.path.exists("watchlist.json"):
            try:
                with open("watchlist.json", encoding="utf-8") as f:
                    watchlist = json.load(f)
            except (json.JSONDecodeError, OSError):
                watchlist = {}
        # Drop comment keys (anything starting with "_")
        watchlist = {k: v for k, v in watchlist.items() if not k.startswith("_")}
        if not watchlist:
            print("watchlist.json empty or missing — nothing to watch.")
            return 0
        cur_lows = {}
        for item_id in watchlist:
            try:
                raw = make_market_fetcher(api_key, item_id)("")
                low = tier2.parse_market(raw)
                if low is not None:
                    cur_lows[item_id] = low
            except TornApiError as e:
                print(f"Market fetch error for {item_id}: {e}", file=sys.stderr)
        prev_lows = load_state("market_state.json") or {}
        events, new_lows = tier2.evaluate_market(prev_lows, cur_lows, watchlist)
        if events:
            notifier = make_telegram_notifier(bot_token, chat_id)
            notifier("\n".join(tier2.format_tier2_event(e) for e in events))
        save_state("market_state.json", new_lows)
        print(f"Market check done. {len(cur_lows)} priced, {len(events)} alert(s) sent.")
        return 0

    if args.check:
        api_key = os.environ.get("TORN_API_KEY")
        if not api_key:
            print("Missing env var: TORN_API_KEY", file=sys.stderr)
            return 2
        try:
            raw = make_torn_fetcher(api_key)("")
        except TornApiError as e:
            print(f"Torn API error: {e}", file=sys.stderr)
            return 3
        print("=== RAW payload from Torn ===")
        print(json.dumps(raw, indent=2))
        snap = parse_snapshot(raw)
        print("\n=== PARSED snapshot (what the rules use) ===")
        print(json.dumps(snap, indent=2))
        # Prove the exact-timing math against live regen fields.
        print("\n=== EXACT TIMING (from Torn's regen clock) ===")
        cfg_marks = config.get("marks", {})
        for bar in ("energy", "nerve"):
            b = snap.get(bar, {})
            reg = snap.get("regen", {}).get(bar, {})
            cur_v, mx = b.get("current", 0), b.get("maximum", 0)
            full = seconds_to_full(cur_v, mx, reg)
            line = (f"{bar:>6}: {cur_v}/{mx}  "
                    f"regen {reg.get('increment')}/{reg.get('interval')}s  "
                    f"full in {fmt_duration(full)}")
            mark = cfg_marks.get(bar)
            if mark is not None:
                to_mark = seconds_to_value(cur_v, mark, reg)
                line += f"  | reaches {mark} in {fmt_duration(to_mark)}"
            print(line)
        print("\nIf the parsed snapshot shows 0s where the raw payload has real "
              "numbers, a field name needs adjusting in parse_snapshot(). If the "
              "timing line shows '?', the regen fields weren't in the payload.")
        return 0

    prev_state = load_state(args.state)

    if args.dry_run:
        # Force alerts by pretending previous state was 'empty', then print.
        printed: list[str] = []
        notifier = lambda text: printed.append(text)  # noqa: E731
        fetcher = lambda _="": _mock_payload()        # noqa: E731
        # Seed prev as empty bars so the mock (full) triggers events.
        seed = parse_snapshot({
            "energy": {"current": 0, "maximum": 100},
            "nerve": {"current": 0, "maximum": 25},
            "happy": {"current": 0, "maximum": 5000},
            "life": {"current": 175, "maximum": 175},
            "cooldowns": {"drug": 10, "medical": 0, "booster": 0},
            "travel": {"time_left": 60, "destination": "Mexico"},
        })
        events, _ = run_once(config, fetcher, notifier, seed)
        if printed:
            print("[dry-run] would send:\n" + "\n".join(printed))
        else:
            print("[dry-run] no alerts for the mock payload.")
        return 0

    api_key = os.environ.get("TORN_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    missing = [n for n, v in [
        ("TORN_API_KEY", api_key),
        ("TELEGRAM_BOT_TOKEN", bot_token),
        ("TELEGRAM_CHAT_ID", chat_id),
    ] if not v]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 2

    fetcher = make_torn_fetcher(api_key)
    notifier = make_telegram_notifier(bot_token, chat_id)

    try:
        tz_offset = int(os.environ.get("TZ_OFFSET_HOURS") or 8)
    except ValueError:
        tz_offset = 8

    # --- Continuous adaptive-timing mode (used by the GitHub Actions poller) ---
    if args.loop is not None:
        deadline = time.monotonic() + max(1, args.loop)
        print(f"[loop] starting adaptive poller for ~{args.loop}s "
              f"(cap={POLL_CAP_SECONDS}s, min={POLL_MIN_SECONDS}s)")
        while True:
            now_utc = datetime.now(timezone.utc)
            quiet = in_quiet_hours(now_utc, config, tz_offset)
            nf = (lambda evs: filter_quiet(evs, now_utc, config, tz_offset)) if quiet else None
            try:
                events, prev_state = run_once(config, fetcher, notifier, prev_state, nf)
                save_state(args.state, prev_state)
                sleep = next_sleep_seconds(prev_state, config)
                en = prev_state.get("energy", {})
                nv = prev_state.get("nerve", {})
                print(f"[loop] {now_utc:%H:%M:%SZ} "
                      f"E={en.get('current')}/{en.get('maximum')} "
                      f"N={nv.get('current')}/{nv.get('maximum')} "
                      f"quiet={'y' if quiet else 'n'} sent={len(events)} "
                      f"next in {fmt_duration_compact(sleep)}")
            except TornApiError as e:
                print(f"[loop] Torn API error: {e}; backing off", file=sys.stderr)
                sleep = POLL_CAP_SECONDS
            except Exception as e:  # noqa: BLE001 — network blip etc.; keep looping
                print(f"[loop] error: {e}; backing off", file=sys.stderr)
                sleep = POLL_CAP_SECONDS
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(max(1, min(sleep, int(remaining))))
        print("[loop] deadline reached; exiting for restart.")
        return 0

    now_utc = datetime.now(timezone.utc)
    quiet = in_quiet_hours(now_utc, config, tz_offset)
    notify_filter = (lambda evs: filter_quiet(evs, now_utc, config, tz_offset)) if quiet else None

    try:
        events, new_state = run_once(config, fetcher, notifier, prev_state, notify_filter)
    except TornApiError as e:
        print(f"Torn API error: {e}", file=sys.stderr)
        return 3

    save_state(args.state, new_state)
    en = new_state.get("energy", {})
    nv = new_state.get("nerve", {})
    reg = new_state.get("regen", {})
    e_full = seconds_to_full(en.get("current", 0), en.get("maximum", 0), reg.get("energy", {}))
    n_full = seconds_to_full(nv.get("current", 0), nv.get("maximum", 0), reg.get("nerve", {}))
    inc = "on" if config["alerts"].get("energy_increase") else "off"
    print(f"[diag] prev_state={'loaded' if prev_state else 'NONE(first-run/baseline)'} "
          f"| energy={en.get('current')}/{en.get('maximum')} (full in {fmt_duration(e_full)}) "
          f"nerve={nv.get('current')}/{nv.get('maximum')} (full in {fmt_duration(n_full)}) "
          f"| increase_mode={inc} | quiet={'yes' if quiet else 'no'} | alerts_found={len(events)}")
    print(f"Polled OK. {len(events)} alert(s) sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
