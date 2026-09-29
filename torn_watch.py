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
import os
import sys
import random
import urllib.parse
import urllib.request
from typing import Callable, Optional

TORN_API_BASE = "https://api.torn.com"
TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_SELECTIONS = "bars,cooldowns,travel"
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
    }


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

    cooldowns = data.get("cooldowns") or {}
    travel = data.get("travel") or {}

    return {
        "energy": bar("energy"),
        "nerve": bar("nerve"),
        "happy": bar("happy"),
        "life": bar("life"),
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
            events.append({
                "type": f"{bar}_full",
                "bar": bar,
                "current": cur_bar.get("current", 0),
                "maximum": cur_bar.get("maximum", 0),
                "threshold": cur_target,
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

    return events, cur


# --------------------------------------------------------------------------
# Message formatting — "Hans" has a laconic underworld-fixer voice, and picks
# a random line each time so alerts don't read identically. Every energy line
# contains "Energy", every nerve line "Nerve", etc. (keeps them greppable).
# --------------------------------------------------------------------------
_LINES = {
    "energy_full": [
        "⚡ Energy's maxed at {current}/{max}. Get to the gym before it spills.",
        "⚡ Full tank — {current}/{max} Energy. Go move some iron, champ.",
        "⚡ {current}/{max} Energy and nowhere to spend it. That's waste. Train.",
        "⚡ Energy topped out ({current}/{max}). The weights are calling.",
    ],
    "nerve_full": [
        "🔴 Nerve's full ({current}/{max}). This city won't rob itself.",
        "🔴 {current}/{max} Nerve — go make some poor life choices.",
        "🔴 Full Nerve at {current}/{max}. Time to earn your reputation.",
        "🔴 Nerve maxed ({current}/{max}). Somewhere, a crime is waiting.",
    ],
    "happy_full": [
        "🙂 Happy's maxed ({current}/{max}) — prime time to train hard.",
        "🙂 {current}/{max} Happy. Your gains will thank you for it.",
    ],
    "energy_increase": [
        "⚡ Energy ticking up — {current}/{max} (+{delta}).",
        "⚡ +{delta} Energy, sitting at {current}/{max} now.",
    ],
    "nerve_increase": [
        "🔴 Nerve creeping up — {current}/{max} (+{delta}).",
        "🔴 +{delta} Nerve, now {current}/{max}.",
    ],
    "drug_cooldown_ended": [
        "💊 Drug cooldown's up — you're clear to dose again.",
        "💊 Cooldown cleared. The pharmacy's open, so to speak.",
    ],
    "medical_cooldown_ended": [
        "🩹 Medical cooldown's up — patch kit's ready when you are.",
    ],
    "booster_cooldown_ended": [
        "🧪 Booster cooldown cleared. Stock up.",
    ],
    "travel_landed": [
        "✈️ Touched down in {dest}. Try to stay out of the papers.",
        "✈️ Landed in {dest} — business awaits.",
        "✈️ You've arrived in {dest}. Watch your back.",
    ],
}


def format_event(ev: dict) -> str:
    t = ev["type"]
    lines = _LINES.get(t)
    if not lines:
        return f"Notice: {t}"
    return random.choice(lines).format(
        current=ev.get("current"),
        max=ev.get("maximum"),
        delta=ev.get("delta"),
        dest=(ev.get("destination") or "your destination"),
    )


def format_message(events: list[dict]) -> str:
    """One Telegram message covering all events from this poll."""
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


def make_telegram_notifier(bot_token: str, chat_id: str) -> Notifier:
    def send(text: str) -> None:
        url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
        payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
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
             prev_state: Optional[dict]) -> tuple[list[dict], dict]:
    """Fetch, evaluate, notify. Returns (events_sent, new_state)."""
    raw = fetcher("")
    snapshot = parse_snapshot(raw)
    events, new_state = evaluate(prev_state, snapshot, config)
    if events:
        notifier(format_message(events))
    return events, new_state


# --------------------------------------------------------------------------
# CLI entry point
# --------------------------------------------------------------------------
def _mock_payload() -> dict:
    """A realistic sample payload for --dry-run (no key needed)."""
    return {
        "energy": {"current": 100, "maximum": 100},
        "nerve": {"current": 25, "maximum": 25},
        "happy": {"current": 4000, "maximum": 5000},
        "life": {"current": 175, "maximum": 175},
        "cooldowns": {"drug": 0, "medical": 0, "booster": 0},
        "travel": {"time_left": 0, "destination": ""},
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
        print("\n=== PARSED snapshot (what the rules use) ===")
        print(json.dumps(parse_snapshot(raw), indent=2))
        print("\nIf the parsed snapshot shows 0s where the raw payload has real "
              "numbers, a field name needs adjusting in parse_snapshot().")
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
        events, new_state = run_once(config, fetcher, notifier, prev_state)
    except TornApiError as e:
        print(f"Torn API error: {e}", file=sys.stderr)
        return 3

    save_state(args.state, new_state)
    en = new_state.get("energy", {})
    nv = new_state.get("nerve", {})
    inc = "on" if config["alerts"].get("energy_increase") else "off"
    print(f"[diag] prev_state={'loaded' if prev_state else 'NONE(first-run/baseline)'} "
          f"| energy={en.get('current')}/{en.get('maximum')} "
          f"nerve={nv.get('current')}/{nv.get('maximum')} "
          f"| increase_mode={inc} | alerts_sent={len(events)}")
    print(f"Polled OK. {len(events)} alert(s) sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
