#!/usr/bin/env python3
"""
Torn Watch — two-way command bot.

The Tier-1 poller (torn_watch.py) pushes alerts TO you. This adds the other
direction: you message the bot and it answers on demand. Still 100% read-only
— every reply just reports your own Torn data; it never performs a game action.

You never have to memorise anything:
  - a persistent button row sits above the keyboard (tap, don't type),
  - Telegram's "/" autocomplete lists every command (setMyCommands),
  - /help spells out what each one does.

Commands:
  /status  - bars, cooldowns, travel right now (with exact "full in …")
  /stats   - battle stats + gain since the last log
  /next    - when energy / nerve will be full (relative + local clock)
  /price   - /price <item_id>  → cheapest market listing for that item
  /help    - the menu

Design mirrors torn_watch: network I/O is injectable, routing/formatting is
pure, so the logic is unit-tested without touching the network.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import torn_watch as tw
import tier2
import travel
import oc as oc_mod

TELEGRAM_API_BASE = "https://api.telegram.org"
DEFAULT_TZ_OFFSET = 8  # Asia/Singapore (UTC+8); override with TZ_OFFSET_HOURS

# Inline buttons (attached to messages, not a bar above the input). Unlike a
# reply keyboard, these don't hijack Android's Back button, and they ride along
# on every reply and on the pinned dashboard. Taps arrive as callback_query
# with the callback_data below, routed through parse_command like typed cmds.
MENU_INLINE = {"inline_keyboard": [
    [{"text": "📊 Status", "callback_data": "status"},
     {"text": "💪 Stats", "callback_data": "stats"}],
    [{"text": "⏱ Next full", "callback_data": "next"},
     {"text": "❓ Help", "callback_data": "help"}],
]}

# Legacy label->command map (old reply-keyboard taps still parse harmlessly).
_LABEL_TO_CMD = {
    "📊 Status": "status",
    "💪 Stats": "stats",
    "⏱ Next full": "next",
    "❓ Help": "help",
}

# Registered with Telegram so typing "/" shows a tappable menu with blurbs.
COMMAND_MENU = [
    ("status", "Bars, cooldowns, travel right now"),
    ("stats", "Battle stats + gain since last log"),
    ("next", "When energy / nerve will be full"),
    ("price", "Cheapest market price: /price <item_id>"),
    ("stock", "Foreign stock + depart time: /stock china"),
    ("watch", "Toggle depart alerts: /watch china on|off"),
    ("oc", "OC reminder: /oc  or  /oc in 12h"),
    ("testland", "Demo the landing buzz (~1 min)"),
    ("help", "Show what I can do"),
]

HELP_TEXT = (
    "🗂 <b>Hans at your service</b>\n"
    "Tap a button or type a command.\n"
    "\n"
    "📊 <b>/status</b>\n"
    "Bars, cooldowns &amp; travel — each with time to full.\n"
    "\n"
    "💪 <b>/stats</b>\n"
    "STR / DEF / SPD / DEX, total, and gain since last log.\n"
    "\n"
    "⏱ <b>/next</b>\n"
    "When energy &amp; nerve hit full (countdown + clock).\n"
    "\n"
    "💰 <b>/price</b> <code>&lt;id&gt;</code>\n"
    "Cheapest market listing. e.g. <code>/price 206</code>\n"
    "\n"
    "📦 <b>/stock</b> <code>&lt;country&gt;</code>\n"
    "Foreign stock + next restock + when to depart. e.g. <code>/stock china</code>\n"
    "\n"
    "👀 <b>/watch</b> <code>&lt;country&gt; on|off</code>\n"
    "Toggle a 'leave now' depart alert. <code>/watch</code> lists them.\n"
    "\n"
    "⚔️ <b>/oc</b>\n"
    "OC reminders (24h/12h/8h). Auto-read, or set: <code>/oc in 12h</code>.\n"
    "\n"
    "❓ <b>/help</b>\n"
    "This menu.\n"
    "\n"
    "<i>I only read your data — you make every move.</i>"
)


# --------------------------------------------------------------------------
# Pure routing
# --------------------------------------------------------------------------
def parse_command(text: str) -> tuple[Optional[str], str]:
    """Map an incoming message to (command, argument).

    Handles typed commands ('/status', '/price 206', '/status@MyBot'),
    bare words ('status'), and button-label taps ('📊 Status').
    Unknown input -> (None, "").
    """
    if not text:
        return None, ""
    text = text.strip()
    if text in _LABEL_TO_CMD:
        return _LABEL_TO_CMD[text], ""
    if text.startswith("/"):
        text = text[1:]
    parts = text.split(maxsplit=1)
    if not parts:
        return None, ""
    cmd = parts[0].lower()
    if "@" in cmd:  # /status@HansBot in groups
        cmd = cmd.split("@", 1)[0]
    arg = parts[1].strip() if len(parts) > 1 else ""
    known = {"status", "stats", "next", "price", "help", "start",
             "preview", "sample", "test", "testland", "buzz",
             "stock", "watch", "oc"}
    if cmd == "start":
        cmd = "help"
    if cmd in ("sample", "test"):
        cmd = "preview"
    if cmd == "buzz":
        cmd = "testland"
    if cmd not in known:
        return None, ""
    return cmd, arg


# --------------------------------------------------------------------------
# Pure formatting
# --------------------------------------------------------------------------
def _bar_row(emoji: str, label: str, b: dict, reg: dict) -> str:
    """One aligned row for the monospace status table (no wrapping)."""
    cur = b.get("current", 0)
    mx = b.get("maximum", 0)
    value = f"{cur}/{mx}"
    if mx and cur >= mx:
        tail = "full"
    else:
        secs = tw.seconds_to_full(cur, mx, reg)
        prefix = ""
        if secs is None and label.strip() == "Life":
            # Life has no API regen clock; estimate from the 5%/5min formula.
            secs = tw.seconds_to_full_life(cur, mx)
            prefix = "~"
        tail = (prefix + tw.fmt_duration_compact(secs)) if secs is not None else "?"
    # label padded to 6, value right-padded to 7 -> columns line up in <pre>
    return f"{emoji} {label:<6}{value:>8}  {tail}"


def format_status(snapshot: dict, now_utc: datetime, tz_offset: int = DEFAULT_TZ_OFFSET) -> str:
    reg = snapshot.get("regen", {})
    local = now_utc + timedelta(hours=tz_offset)
    rows = [
        _bar_row("⚡", "Energy", snapshot.get("energy", {}), reg.get("energy", {})),
        _bar_row("🔴", "Nerve", snapshot.get("nerve", {}), reg.get("nerve", {})),
        _bar_row("🙂", "Happy", snapshot.get("happy", {}), reg.get("happy", {})),
        _bar_row("❤️", "Life", snapshot.get("life", {}), reg.get("life", {})),
    ]
    out = [f"🗂 <b>Hans Report</b> · {local:%H:%M}", "<pre>" + "\n".join(rows) + "</pre>"]

    # Hospital / jail line — only shown when it applies (hidden when Okay).
    st = snapshot.get("status", {})
    cat = tw._state_category(st.get("state", ""))
    until = st.get("until", 0)
    if cat in ("hospital", "jail") and until:
        remaining = int(until - now_utc.timestamp())
        if remaining > 0:
            label = "🏥 <b>In hospital</b>" if cat == "hospital" else "🚔 <b>In jail</b>"
            out.append(f"{label} · out in {tw.fmt_duration_compact(remaining)}")

    # Cooldowns: only name the ones still ticking; otherwise one short line.
    cd = snapshot.get("cooldowns", {})
    busy = []
    for key, name in (("drug", "Drug"), ("medical", "Med"), ("booster", "Boost")):
        secs = cd.get(key, 0)
        if secs > 0:
            busy.append(f"{name} {tw.fmt_duration_compact(secs)}")
    out.append("⏲ Cooldowns: " + ("all clear ✅" if not busy else " · ".join(busy)))

    tv = snapshot.get("travel", {})
    if tv.get("time_left", 0) > 0:
        dest = tw.html_escape(tv.get("destination") or "somewhere")
        out.append(f"✈️ To <b>{dest}</b> · lands {tw.fmt_duration_compact(tv['time_left'])}")
    return "\n".join(out)


def format_next(snapshot: dict, now_utc: datetime, tz_offset: int = DEFAULT_TZ_OFFSET) -> str:
    reg = snapshot.get("regen", {})
    out = ["⏱ <b>Next full</b>"]
    for emoji, label, key in (("⚡", "Energy", "energy"),
                              ("🔴", "Nerve", "nerve"),
                              ("❤️", "Life", "life")):
        b = snapshot.get(key, {})
        cur, mx = b.get("current", 0), b.get("maximum", 0)
        secs = tw.seconds_to_full(cur, mx, reg.get(key, {}))
        approx = ""
        if secs is None and key == "life":
            secs = tw.seconds_to_full_life(cur, mx)   # estimate, no API clock
            approx = "≈"
        if mx and cur >= mx:
            out.append(f"{emoji} <b>{label}</b> — already full ({cur}/{mx})")
        elif secs:
            clock = now_utc + timedelta(hours=tz_offset, seconds=secs)
            out.append(f"{emoji} <b>{label}</b> {cur}/{mx} — "
                       f"{approx}{tw.fmt_duration_compact(secs)} (≈{clock:%H:%M})")
        else:
            out.append(f"{emoji} <b>{label}</b> {cur}/{mx}")
    return "\n".join(out)


def format_stats(stats: dict, prev_row: Optional[dict]) -> str:
    def g(k):
        return f"{stats.get(k, 0):,}"
    out = [
        "💪 <b>Battle stats</b>", "",
        f"<code>STR {g('strength'):>13}</code>",
        f"<code>DEF {g('defense'):>13}</code>",
        f"<code>SPD {g('speed'):>13}</code>",
        f"<code>DEX {g('dexterity'):>13}</code>",
        f"<b><code>TOT {g('total'):>13}</code></b>",
    ]
    if prev_row:
        try:
            gained = stats.get("total", 0) - int(prev_row.get("total", 0))
            when = prev_row.get("timestamp", "last log")[:16].replace("T", " ")
            sign = "+" if gained >= 0 else ""
            out += ["", f"<i>{sign}{gained:,} total since {when} UTC</i>"]
        except (TypeError, ValueError):
            pass
    return "\n".join(out)


def format_price(item_id: str, name: Optional[str], low: Optional[int]) -> str:
    who = tw.html_escape(name) if name else f"item {tw.html_escape(item_id)}"
    if low is None:
        return (f"💰 Couldn't read a market price for <b>{who}</b> right now "
                f"(no listings, or a bad item id).")
    return f"💰 <b>{who}</b> cheapest listing: <code>${low:,}</code>"


# --------------------------------------------------------------------------
# Network I/O (injectable)
# --------------------------------------------------------------------------
def _tg_call(bot_token: str, method: str, payload: dict) -> dict:
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "torn-watch-bot/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        # Telegram returns 400 with a description for things like
        # "message is not modified" / "message to edit not found" — hand the
        # parsed error back instead of raising so callers can react.
        try:
            return json.loads(e.read().decode("utf-8"))
        except Exception:
            return {"ok": False, "description": str(e)}


def set_my_commands(bot_token: str) -> None:
    cmds = [{"command": c, "description": d} for c, d in COMMAND_MENU]
    try:
        _tg_call(bot_token, "setMyCommands", {"commands": cmds})
    except Exception as e:  # non-fatal: the bot still works without the menu
        print(f"[bot] setMyCommands failed (non-fatal): {e}", file=sys.stderr)


def send_reply(bot_token: str, chat_id: str, text: str, with_keyboard: bool = True) -> None:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if with_keyboard:
        payload["reply_markup"] = MENU_INLINE
    _tg_call(bot_token, "sendMessage", payload)


def send_photo_reply(bot_token: str, chat_id: str, photo_url: str, caption: str) -> None:
    """Send an image with caption + the inline menu. Falls back to a plain text
    reply if Telegram can't fetch the image (bad/missing URL), so /price never
    comes back broken."""
    r = _tg_call(bot_token, "sendPhoto", {
        "chat_id": chat_id, "photo": photo_url, "caption": caption,
        "parse_mode": "HTML", "reply_markup": MENU_INLINE,
    })
    if not r.get("ok"):
        send_reply(bot_token, chat_id, caption)


def answer_callback(bot_token: str, cq_id: str) -> None:
    """Acknowledge a button tap so Telegram stops its loading spinner."""
    _tg_call(bot_token, "answerCallbackQuery", {"callback_query_id": cq_id})


def get_updates(bot_token: str, offset: Optional[int], timeout: int = 30) -> list[dict]:
    params = {"timeout": timeout,
              "allowed_updates": json.dumps(["message", "callback_query"])}
    if offset is not None:
        params["offset"] = offset
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/getUpdates?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "torn-watch-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout + 15) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("result", []) if data.get("ok") else []


# --------------------------------------------------------------------------
# Travel-landing buzzer
# --------------------------------------------------------------------------
# Fires the instant you touch down ABROAD and keeps buzzing (a fresh ping every
# few seconds) until you tap "✅ Landed", so you buy and leave fast instead of
# idling in a foreign country where you get mugged. Timing is exact: the moment
# we see you're travelling, Torn tells us the seconds left, so we know your
# landing second in advance and alert right then — no poll lag.
LAND_STATE_FILE = "travel_state.json"
LAND_ACK_DATA = "ack_landing"
LAND_ACK_MARKUP = {"inline_keyboard": [[
    {"text": "✅ Landed — stop buzzing", "callback_data": LAND_ACK_DATA}]]}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


LAND_BUZZ_INTERVAL = _env_int("LAND_BUZZ_INTERVAL", 2)   # seconds between buzzes
LAND_BUZZ_MAX = _env_int("LAND_BUZZ_MAX", 60)            # demo (/testland) duration
LAND_LEAD_SECONDS = _env_int("LAND_LEAD_SECONDS", 60)    # warn this long BEFORE landing
LAND_POST_SECONDS = _env_int("LAND_POST_SECONDS", 60)    # keep buzzing this long AFTER
TRAVEL_POLL_SECONDS = _env_int("TRAVEL_POLL_SECONDS", 30)  # how often to recheck travel


def _load_land(path: str = LAND_STATE_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_land(d: dict, path: str = LAND_STATE_FILE) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def is_foreign(destination: str) -> bool:
    """A real foreign landing (where you buy & risk mugging), not the trip home.
    Torn reports the home arrival as 'Torn'; anything else is abroad."""
    d = (destination or "").strip().lower()
    return bool(d) and d != "torn"


def fetch_travel(api_key: str) -> tuple[int, str]:
    """(seconds_until_landing, destination). 0 seconds => not travelling."""
    raw = tw.make_torn_fetcher(api_key, "travel")("")
    tv = tw.parse_snapshot(raw).get("travel", {})
    return int(tv.get("time_left", 0) or 0), (tv.get("destination") or "")


def landing_timeout(now: float, arrival: Optional[float], alerted: bool,
                    buzzing: bool, base: int = 30,
                    interval: int = LAND_BUZZ_INTERVAL) -> int:
    """How long to let getUpdates block this cycle, so we (a) re-buzz on time
    while buzzing and (b) wake right at touchdown when a landing is pending."""
    if buzzing:
        return max(1, min(interval, 5))
    if arrival is not None and not alerted:
        secs = arrival - now
        return 1 if secs <= 0 else max(1, min(base, int(secs)))
    return base


def land_buzz_text(destination: str) -> str:
    d = tw.html_escape(destination or "your destination")
    return (f"🛬 <b>{d} — landing in ~1 min!</b>\n"
            f"Get your buy ready — hit it the second you land, then bounce "
            f"(don't idle = mug risk).\n"
            f"<i>Tap ✅ below to stop the buzzing.</i>")


def send_land_buzz(bot_token: str, chat_id: str, destination: str) -> None:
    _tg_call(bot_token, "sendMessage", {
        "chat_id": chat_id,
        "text": land_buzz_text(destination),
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "reply_markup": LAND_ACK_MARKUP,
    })


# --------------------------------------------------------------------------
# Pinned live dashboard — one message the bot edits in place, so there's a
# always-current status at the top of the chat instead of a stream of pings.
# --------------------------------------------------------------------------
PIN_STATE_FILE = "pin_state.json"


def _load_pin(path: str = PIN_STATE_FILE) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_pin(d: dict, path: str = PIN_STATE_FILE) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def _send_and_pin(bot_token: str, chat_id: str, text: str) -> Optional[int]:
    r = _tg_call(bot_token, "sendMessage", {
        "chat_id": chat_id, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
        "reply_markup": MENU_INLINE,
    })
    mid = (r.get("result") or {}).get("message_id")
    if mid:
        _tg_call(bot_token, "pinChatMessage", {
            "chat_id": chat_id, "message_id": mid, "disable_notification": True,
        })
    return mid


def refresh_pin(api_key: str, bot_token: str, chat_id: str, tz_offset: int) -> None:
    """Create (and pin) or edit the live dashboard message. Best-effort."""
    raw = tw.make_torn_fetcher(api_key, "bars,cooldowns,travel,profile")("")
    snap = tw.parse_snapshot(raw)
    text = "📌 " + format_status(snap, datetime.now(timezone.utc), tz_offset)

    st = _load_pin()
    mid = st.get("message_id")
    if st.get("last_text") == text and mid:
        return  # unchanged since last edit — skip (also avoids "not modified")

    if mid:
        r = _tg_call(bot_token, "editMessageText", {
            "chat_id": chat_id, "message_id": mid, "text": text,
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": MENU_INLINE,
        })
        if not r.get("ok"):
            desc = (r.get("description") or "").lower()
            if "not modified" in desc:
                st.update({"message_id": mid, "last_text": text})
                _save_pin(st)
                return
            mid = _send_and_pin(bot_token, chat_id, text)  # gone — recreate
    else:
        mid = _send_and_pin(bot_token, chat_id, text)

    if mid:
        st.update({"message_id": mid, "last_text": text})
        _save_pin(st)


# --------------------------------------------------------------------------
# Travel watchlist + flight-time learning + OC state (JSON-backed)
# --------------------------------------------------------------------------
WATCHLIST_FILE = "depart_watch.json"   # separate from the /price watchlist.json
FLIGHTS_FILE = "flights.json"          # learned one-way flight seconds per country
OC_FILE = "oc_state.json"


def _load_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return default


def _save_json(path: str, data) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def load_watchlist() -> dict:
    """{code: {enabled: bool, item: str}}. Seeds China/Panda ON the first time."""
    wl = _load_json(WATCHLIST_FILE, None)
    if not isinstance(wl, dict) or not wl:
        wl = {"chi": {"enabled": True, "item": "Panda Plushie"}}
        _save_json(WATCHLIST_FILE, wl)
    return wl


def watch_command(arg: str) -> str:
    """/watch  -> list; /watch china on|off [item] -> toggle a route."""
    wl = load_watchlist()
    parts = (arg or "").split()
    if not parts:
        if not wl:
            return "👀 <b>Watchlist empty.</b> Add one: <code>/watch china on</code>"
        rows = []
        for code, cfg in wl.items():
            mark = "🟢 on" if cfg.get("enabled") else "⚪️ off"
            rows.append(f"{mark} · <b>{travel.country_name(code)}</b> "
                        f"({tw.html_escape(cfg.get('item') or 'any')})")
        return ("👀 <b>Depart watchlist</b>\n" + "\n".join(rows)
                + "\n\n<i>Toggle: /watch china off</i>")
    code = travel.code_for(parts[0])
    if not code:
        return (f"🤷 Don't know '{tw.html_escape(parts[0])}'. Try a country like "
                f"china, mexico, uae…")
    cfg = wl.get(code, {"enabled": False, "item": ""})
    if len(parts) >= 2 and parts[1].lower() in ("on", "off"):
        cfg["enabled"] = (parts[1].lower() == "on")
    else:
        cfg["enabled"] = not cfg.get("enabled")  # bare /watch china flips it
    if len(parts) >= 3:
        cfg["item"] = " ".join(parts[2:])
    cfg.setdefault("item", "")
    wl[code] = cfg
    _save_json(WATCHLIST_FILE, wl)
    state = "ON 🟢 — I'll ping you when to depart" if cfg["enabled"] else "OFF ⚪️"
    return (f"👀 <b>{travel.country_name(code)}</b> depart alert now <b>{state}</b>"
            + (f"\nItem: {tw.html_escape(cfg['item'])}" if cfg.get("item") else ""))


def learn_flight(code: str, seconds: int) -> None:
    """Record a one-way flight time (keep the longest seen — the start of a
    trip is the full duration; a mid-trip reading is shorter)."""
    if not code or seconds <= 0:
        return
    flights = _load_json(FLIGHTS_FILE, {})
    if seconds > int(flights.get(code, 0)):
        flights[code] = int(seconds)
        _save_json(FLIGHTS_FILE, flights)


def flight_seconds(code: str) -> Optional[int]:
    v = _load_json(FLIGHTS_FILE, {}).get(code)
    return int(v) if v else None


CYCLES_FILE = "cycles.json"   # learned restock cycle length per country


def observe_restock(code: str, restock_epoch: int) -> None:
    """Learn the restock cycle by watching nextRestock advance between polls.
    When the restock timestamp jumps forward by a sane amount (5min–3h), that
    gap is the cycle — stored so depart-timing can roll to a reachable restock."""
    if not code or not restock_epoch:
        return
    data = _load_json(CYCLES_FILE, {})
    row = data.get(code) or {}
    last = row.get("last")
    if last and restock_epoch > last:
        delta = restock_epoch - last
        if 300 <= delta <= 3 * 3600:
            row["cycle"] = int(delta)
    row["last"] = int(restock_epoch)
    data[code] = row
    _save_json(CYCLES_FILE, data)


def cycle_seconds(code: str) -> Optional[int]:
    row = _load_json(CYCLES_FILE, {}).get(code) or {}
    c = row.get("cycle")
    return int(c) if c else None


def _parse_when(arg: str, tz_offset: int) -> Optional[int]:
    """Parse '/oc' time input into epoch seconds. Accepts 'in 6h' / 'in 90m',
    'DD/MM HH:MM' or 'HH:MM' (interpreted in your local tz)."""
    s = (arg or "").strip().lower()
    now = datetime.now(timezone.utc)
    if s.startswith("in "):
        s = s[3:].strip()
        try:
            if s.endswith("h"):
                return int(now.timestamp() + float(s[:-1]) * 3600)
            if s.endswith("m"):
                return int(now.timestamp() + float(s[:-1]) * 60)
        except ValueError:
            return None
    local = now + timedelta(hours=tz_offset)
    for fmt in ("%d/%m %H:%M", "%d/%m/%y %H:%M", "%H:%M"):
        try:
            t = datetime.strptime(s, fmt)
        except ValueError:
            continue
        when = local.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        if fmt != "%H:%M":
            when = when.replace(month=t.month, day=t.day)
        if when < local:               # already passed today -> next day
            when = when + timedelta(days=1)
        return int((when - timedelta(hours=tz_offset)).timestamp())
    return None


def oc_command(arg: str, tz_offset: int) -> str:
    """/oc            -> show the tracked OC time
       /oc off|clear  -> stop manual tracking (auto-read, if any, resumes)
       /oc in 6h | /oc 21/10 14:00 -> set it manually."""
    st = _load_json(OC_FILE, {})
    a = (arg or "").strip().lower()
    if not a:
        rd = st.get("ready")
        if not rd:
            return ("🕵️ No OC time tracked yet. I'll auto-read it from the faction "
                    "API; or set it: <code>/oc in 18h</code> or <code>/oc 21/10 14:00</code>.")
        local = datetime.fromtimestamp(rd, timezone.utc) + timedelta(hours=tz_offset)
        src = st.get("source", "auto")
        return f"🕵️ OC ready ≈ <b>{local:%d/%m %H:%M}</b> (your time, {src})."
    if a in ("off", "clear", "stop"):
        _save_json(OC_FILE, {})
        return "🕵️ OC tracking cleared."
    when = _parse_when(arg, tz_offset)
    if not when:
        return ("🤷 Couldn't read that time. Try <code>/oc in 12h</code> or "
                "<code>/oc 21/10 14:00</code>.")
    _save_json(OC_FILE, {"ready": when, "source": "manual", "fired": []})
    local = datetime.fromtimestamp(when, timezone.utc) + timedelta(hours=tz_offset)
    return (f"🕵️ OC reminder set for ≈ <b>{local:%d/%m %H:%M}</b> (your time). "
            f"I'll ping at 24h / 12h / 8h before.")


_OC_LOGGED = {"done": False}


def _fetch_faction_crimes(api_key: str) -> Optional[dict]:
    """GET v2 faction crimes (OC 2.0). None if the key can't read it (no faction
    access) or the call fails — caller falls back to manual /oc."""
    params = urllib.parse.urlencode({"key": api_key})
    url = f"{tw.TORN_API_BASE}/v2/faction/crimes?{params}"
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "torn-watch/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:  # noqa: BLE001
        return None
    if isinstance(data, dict) and data.get("error"):
        return None
    return data if isinstance(data, dict) else None


def _my_user_id(api_key: str) -> int:
    try:
        prof = tw.make_torn_fetcher(api_key, "profile")("")
        return int(prof.get("player_id") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _auto_read_oc(api_key: str) -> Optional[int]:
    """Best-effort read of your current OC's ready time from the faction API.
    Logs the raw crime payload once so the exact field can be confirmed."""
    raw = _fetch_faction_crimes(api_key)
    if raw is None:
        return None
    if not _OC_LOGGED["done"]:
        print("[bot] OC crimes sample:", json.dumps(raw)[:1200], file=sys.stderr)
        _OC_LOGGED["done"] = True
    return oc_mod.parse_my_oc_ready(raw, _my_user_id(api_key))


def _restock_and_cycle(code: str, it: dict, model: dict, now: float) -> tuple:
    """(next_restock_epoch, cycle_seconds) for an item — preferring the restock
    model (interval + last restock), falling back to the feed's own field and a
    self-learned cycle."""
    interval, last = travel.model_interval_last(model, code, it.get("id"))
    if interval and last:
        return (travel.next_restock(last, interval, now), int(interval))
    return (it.get("restock"), cycle_seconds(code))


def format_stock(code: str, parsed: dict, item_needle: str, now_utc: datetime,
                 tz_offset: int, model: Optional[dict] = None) -> str:
    """Live stock + projected next restock + computed depart time for a country."""
    name = travel.country_name(code)
    it = travel.find_item(parsed, code, item_needle)
    if not it:
        return (f"📦 No stock data for <b>{name}</b> right now "
                f"(feed may be briefly down — try again).")
    now = now_utc.timestamp()
    lines = [f"📦 <b>{name} — {tw.html_escape(it['name'] or 'item')}</b>",
             f"Stock: <b>{it['qty']:,}</b>   Buy: <code>${it['cost']:,}</code>"]
    rs, cyc = _restock_and_cycle(code, it, model or {}, now)
    if rs:
        local = datetime.fromtimestamp(rs, timezone.utc) + timedelta(hours=tz_offset)
        when = "now" if rs <= now else tw.fmt_duration_compact(int(rs - now))
        lines.append(f"Next restock: <b>{local:%H:%M}</b> (in {when})")
        fs = flight_seconds(code)
        if fs:
            dep = travel.next_catchable_depart(rs, fs, now, cyc)
            if dep:
                dloc = datetime.fromtimestamp(dep, timezone.utc) + timedelta(hours=tz_offset)
                lines.append(f"🛫 Depart <b>{dloc:%H:%M}</b> "
                             f"(in {tw.fmt_duration_compact(max(0, int(dep - now)))}) "
                             f"→ land ~2m after a restock")
            else:
                lines.append("🛫 Next restock's too soon to catch — "
                             "I'll aim you at a later one once I know the cycle.")
        else:
            lines.append("🛫 (fly there once and I'll learn your flight time → "
                         "then exact depart times)")
    else:
        lines.append("<i>No restock timing available yet.</i>")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Command handling
# --------------------------------------------------------------------------
def _preview_message() -> str:
    """Render one of each alert exactly as the poller would send it, so you can
    eyeball the formatting on demand instead of waiting for a real crossing."""
    samples = [
        {"type": "energy_mark", "bar": "energy", "current": 25, "maximum": 100,
         "mark": 25, "eta_full": 13440},
        {"type": "energy_overflow", "bar": "energy", "current": 95, "maximum": 100,
         "eta_full": 120},
        {"type": "nerve_overflow", "bar": "nerve", "current": 23, "maximum": 25,
         "eta_full": 480},
        {"type": "energy_full", "bar": "energy", "current": 100, "maximum": 100},
        {"type": "hospital_in",
         "until": int(datetime.now(timezone.utc).timestamp()) + 720},
        {"type": "milestone", "value": 50000, "total": 61250},
        {"type": "travel_landed", "destination": "Switzerland"},
    ]
    body = "\n\n".join(tw.format_event(e) for e in samples)
    return "👁 <b>Alert format preview</b>\n<i>(samples — not live values)</i>\n\n" + body


def item_image_url(item_id: str, api_image: Optional[str]) -> str:
    """Prefer the image URL the API gave us; else Torn's standard item image."""
    if api_image:
        return api_image
    return f"https://www.torn.com/images/items/{item_id}/large.png"


def handle(cmd: str, arg: str, api_key: str, now_utc: datetime,
           tz_offset: int = DEFAULT_TZ_OFFSET) -> tuple[str, Optional[str]]:
    """Produce (reply_text, photo_url) for one command. photo_url is set only
    for /price (the item image); everything else returns (text, None). Network
    reads happen here; any API hiccup becomes a friendly message, never a crash."""
    if cmd == "help":
        return HELP_TEXT, None
    if cmd == "preview":
        return _preview_message(), None
    if cmd == "watch":
        return watch_command(arg), None
    if cmd == "oc":
        return oc_command(arg, tz_offset), None
    try:
        if cmd == "stock":
            code = travel.code_for(arg.split()[0]) if arg.split() else "chi"
            if not code:
                return ("📦 Which country? e.g. <code>/stock china</code>, "
                        "<code>/stock mexico</code>.", None)
            wl = load_watchlist().get(code, {})
            needle = " ".join(arg.split()[1:]) or wl.get("item", "")
            parsed = travel.fetch_stock()
            if not parsed:
                return ("📦 Stock feed is unreachable right now — try again in a "
                        "moment.", None)
            model = travel.fetch_restock_model()
            return format_stock(code, parsed, needle, now_utc, tz_offset, model), None
    except Exception as e:  # noqa: BLE001
        return f"⚠️ Stock lookup hiccup ({tw.html_escape(str(e))}).", None
    try:
        if cmd in ("status", "next"):
            sel = "bars,cooldowns,travel,profile" if cmd == "status" else "bars"
            raw = tw.make_torn_fetcher(api_key, sel)("")
            snap = tw.parse_snapshot(raw)
            fmt = format_status if cmd == "status" else format_next
            return fmt(snap, now_utc, tz_offset), None
        if cmd == "stats":
            raw = tw.make_torn_fetcher(api_key, "battlestats")("")
            stats = tier2.parse_battlestats(raw)
            return format_stats(stats, _last_history_row()), None
        if cmd == "price":
            if not arg.isdigit():
                return ("💰 Give me an item id, e.g. <code>/price 206</code> "
                        "(find it in the item's market URL).", None)
            raw = tw.make_market_fetcher(api_key, arg)("")
            low = tier2.parse_market(raw)
            meta = tier2.parse_market_item(raw)
            name = meta["name"] or _watchlist_name(arg)
            caption = format_price(arg, name, low)
            photo = item_image_url(arg, meta["image"]) if low is not None else None
            return caption, photo
    except tw.TornApiError as e:
        return f"⚠️ Torn API said: {tw.html_escape(str(e.message))}", None
    except Exception as e:  # noqa: BLE001 — never let one bad command kill the loop
        return f"⚠️ Something went wrong reading that ({tw.html_escape(str(e))}).", None
    return "", None


def _last_history_row() -> Optional[dict]:
    """Most recent row of history.csv (for stat-gain deltas), if present."""
    path = "history.csv"
    if not os.path.exists(path):
        return None
    try:
        import csv
        with open(path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows[-1] if rows else None
    except (OSError, csv.Error):
        return None


def _watchlist_name(item_id: str) -> Optional[str]:
    """Friendly name for an item id if it's in watchlist.json."""
    if not os.path.exists("watchlist.json"):
        return None
    try:
        with open("watchlist.json", encoding="utf-8") as f:
            wl = json.load(f)
        entry = wl.get(item_id)
        return entry.get("name") if isinstance(entry, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------
# Offset persistence (so a restart doesn't replay old messages)
# --------------------------------------------------------------------------
OFFSET_FILE = "commands_state.json"


def load_offset(path: str = OFFSET_FILE) -> Optional[int]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("offset")
    except (OSError, json.JSONDecodeError):
        return None


def save_offset(offset: int, path: str = OFFSET_FILE) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"offset": offset}, f)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Torn Watch — two-way command bot.")
    p.add_argument("--once", action="store_true",
                   help="Drain pending updates once and exit (for testing / cron).")
    p.add_argument("--seconds", type=int, default=int(os.environ.get("BOT_RUN_SECONDS", "18000")),
                   help="How long to keep long-polling before exiting (default ~5h).")
    p.add_argument("--hello", action="store_true",
                   help="Send the welcome message + button keyboard, then exit.")
    args = p.parse_args(argv)

    api_key = os.environ.get("TORN_API_KEY")
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    try:
        # an unset GitHub Variable arrives as "" — fall back, don't crash
        tz_offset = int(os.environ.get("TZ_OFFSET_HOURS") or DEFAULT_TZ_OFFSET)
    except ValueError:
        tz_offset = DEFAULT_TZ_OFFSET
    missing = [n for n, v in [("TORN_API_KEY", api_key),
                              ("TELEGRAM_BOT_TOKEN", bot_token),
                              ("TELEGRAM_CHAT_ID", chat_id)] if not v]
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 2

    set_my_commands(bot_token)

    # One-time: clear the old persistent button bar left by earlier versions so
    # Android's Back button behaves (the menu is inline now). Flag persists in
    # pin_state.json so we don't nag on every restart.
    _pin_state = _load_pin()
    if not _pin_state.get("kbd_cleared"):
        try:
            _tg_call(bot_token, "sendMessage", {
                "chat_id": chat_id,
                "text": "🔘 Menu buttons now attach to messages (and the pinned report), "
                        "so your Back button works normally again.",
                "reply_markup": {"remove_keyboard": True},
            })
        except Exception as e:  # noqa: BLE001
            print(f"[bot] keyboard clear failed (non-fatal): {e}", file=sys.stderr)
        _pin_state["kbd_cleared"] = True
        _save_pin(_pin_state)

    if args.hello:
        send_reply(bot_token, chat_id,
                   "👋 <b>Hans is online.</b> Tap a button below or type /help.")
        return 0

    offset = load_offset()
    deadline = time.time() + max(1, args.seconds)
    pin_on = os.environ.get("PIN_STATUS", "1").strip().lower() not in ("0", "false", "no", "off")
    try:
        pin_every = int(os.environ.get("PIN_REFRESH_SECONDS") or 600)
    except ValueError:
        pin_every = 600
    last_pin = 0.0

    # Travel-landing buzzer state (survives restart via travel_state.json).
    _land = _load_land()
    land_arrival = _land.get("arrival")       # epoch secs of touchdown, or None
    land_dest = _land.get("dest", "")
    land_alerted = bool(_land.get("alerted", False))
    last_travel = 0.0
    buzzing = False
    buzz_next = buzz_stop = 0.0

    # Depart-watch + OC-reminder background state.
    last_depart = 0.0
    depart_fired: dict = {}           # code -> restock epoch we last pinged for
    last_oc = 0.0
    try:
        depart_poll = int(os.environ.get("DEPART_POLL_SECONDS") or 90)
    except ValueError:
        depart_poll = 90
    try:
        oc_poll = int(os.environ.get("OC_POLL_SECONDS") or 300)
    except ValueError:
        oc_poll = 300

    print(f"[bot] listening (offset={offset}, until +{args.seconds}s, "
          f"once={args.once}, pin={'on' if pin_on else 'off'})")

    while True:
        now = time.time()

        # --- Travel-landing watch (skip while already buzzing / in --once) ---
        if not args.once and not buzzing and (now - last_travel) >= TRAVEL_POLL_SECONDS:
            try:
                tl, dest = fetch_travel(api_key)
                if tl > 0 and is_foreign(dest):
                    learn_flight(travel.code_for(dest) or "", tl)  # learn flight time
                    new_arrival = now + tl
                    # (re)arm on a new or changed outbound foreign flight
                    if (land_arrival is None or dest != land_dest
                            or abs(float(land_arrival) - new_arrival) > 8):
                        land_arrival, land_dest, land_alerted = new_arrival, dest, False
                        _save_land({"arrival": land_arrival, "dest": land_dest,
                                    "alerted": False})
                elif tl == 0:
                    # not travelling (or heading home): drop a landing we've
                    # already fired, or a stale one; keep a just-passed unfired
                    # arrival briefly so the trigger below still catches it.
                    if land_arrival is not None and (
                            land_alerted or now > float(land_arrival) + 20):
                        land_arrival, land_dest, land_alerted = None, "", False
                        _save_land({})
            except Exception as e:  # noqa: BLE001 — never let a check kill the loop
                print(f"[bot] travel check error (non-fatal): {e}", file=sys.stderr)
            last_travel = now

        # Start buzzing ~1 min BEFORE touchdown so you're poised to buy the
        # instant you land, and keep buzzing a bit past landing until you ack.
        if (not args.once and not buzzing and land_arrival is not None
                and not land_alerted and now >= float(land_arrival) - LAND_LEAD_SECONDS):
            buzzing = True
            buzz_next = now
            buzz_stop = float(land_arrival) + LAND_POST_SECONDS
            land_alerted = True
            _save_land({"arrival": land_arrival, "dest": land_dest, "alerted": True})

        # Buzz on cadence; give up at the cap.
        if buzzing:
            if now >= buzz_stop:
                buzzing = False
                land_arrival, land_dest, land_alerted = None, "", False
                _save_land({})
                send_reply(bot_token, chat_id,
                           "🔕 Stopped buzzing (timed out). You've landed — go buy!")
            elif now >= buzz_next:
                try:
                    send_land_buzz(bot_token, chat_id, land_dest)
                except Exception as e:  # noqa: BLE001
                    print(f"[bot] land buzz error (non-fatal): {e}", file=sys.stderr)
                buzz_next = now + LAND_BUZZ_INTERVAL

        # --- Auto-depart watch: ping when it's time to leave for a watched
        # country (enabled routes only; quiet while a flight's already underway).
        if (not args.once and not buzzing and land_arrival is None
                and (now - last_depart) >= depart_poll):
            try:
                enabled = {c: cfg for c, cfg in load_watchlist().items()
                           if cfg.get("enabled")}
                parsed = travel.fetch_stock() if enabled else {}
                model = travel.fetch_restock_model() if enabled else {}
                for code, cfg in enabled.items():
                    it = travel.find_item(parsed, code, cfg.get("item", ""))
                    if not it:
                        continue
                    rs, cyc = _restock_and_cycle(code, it, model, now)
                    if rs:
                        observe_restock(code, rs)   # keep a learned-cycle fallback fresh
                    fs = flight_seconds(code)
                    if not (rs and fs):
                        continue
                    dep = travel.next_catchable_depart(rs, fs, now, cyc)
                    if dep is None:
                        continue
                    # fire once per restock, within ~one poll window of depart
                    if abs(dep - now) <= depart_poll and depart_fired.get(code) != rs:
                        depart_fired[code] = rs
                        send_reply(bot_token, chat_id,
                                   f"🛫 <b>Leave for {travel.country_name(code)} now!</b>\n"
                                   f"{tw.html_escape(it.get('name') or 'stock')}: "
                                   f"land ~2m after restock (~{it['qty']:,} in stock). "
                                   f"Buy &amp; bounce.")
            except Exception as e:  # noqa: BLE001
                print(f"[bot] depart watch error (non-fatal): {e}", file=sys.stderr)
            last_depart = now

        # --- OC reminders: auto-read the ready time (best effort) unless you've
        # set it manually; ping at 24h / 12h / 8h before.
        if not args.once and (now - last_oc) >= oc_poll:
            try:
                st = _load_json(OC_FILE, {})
                if st.get("source") != "manual":
                    ready = _auto_read_oc(api_key)
                    if ready and ready != st.get("ready"):
                        st = {"ready": ready, "source": "auto",
                              "fired": st.get("fired", [])}
                        _save_json(OC_FILE, st)
                ready = st.get("ready")
                if ready:
                    fired = st.get("fired", [])
                    due = oc_mod.due_reminders(ready, now, fired=fired)
                    for off in due:
                        local = (datetime.fromtimestamp(ready, timezone.utc)
                                 + timedelta(hours=tz_offset))
                        send_reply(bot_token, chat_id,
                                   f"⚔️ <b>OC in ~{oc_mod.fmt_offset(off)}</b> — "
                                   f"ready ≈ {local:%d/%m %H:%M} your time. "
                                   f"Don't forget to commit it!")
                        fired.append(off)
                    if due:
                        st["fired"] = fired
                        _save_json(OC_FILE, st)
                    if ready < now - 3600:   # OC passed -> reset for the next one
                        _save_json(OC_FILE, {})
            except Exception as e:  # noqa: BLE001
                print(f"[bot] oc reminder error (non-fatal): {e}", file=sys.stderr)
            last_oc = now

        # Keep the pinned dashboard fresh (best-effort, never blocks commands).
        if pin_on and time.time() - last_pin >= pin_every:
            try:
                refresh_pin(api_key, bot_token, chat_id, tz_offset)
            except Exception as e:  # noqa: BLE001
                print(f"[bot] pin refresh error (non-fatal): {e}", file=sys.stderr)
            last_pin = time.time()

        # Wake ~1 min before touchdown (the lead), not at touchdown.
        _land_target = (float(land_arrival) - LAND_LEAD_SECONDS
                        if land_arrival is not None else None)
        poll_to = 0 if args.once else landing_timeout(
            now, _land_target, land_alerted, buzzing)
        try:
            updates = get_updates(bot_token, offset, timeout=poll_to)
        except Exception as e:  # network blip — pause and retry
            print(f"[bot] getUpdates error: {e}", file=sys.stderr)
            time.sleep(5)
            if args.once or time.time() >= deadline:
                break
            continue

        for u in updates:
            offset = u["update_id"] + 1
            # Normalise a message or a button tap into (from_chat, text, cq_id).
            text, cq_id, from_chat = None, None, None
            if "message" in u:
                m = u["message"]
                from_chat = (m.get("chat") or {}).get("id")
                text = m.get("text", "")
            elif "callback_query" in u:
                cq = u["callback_query"]
                cq_id = cq.get("id")
                from_chat = ((cq.get("message") or {}).get("chat") or {}).get("id")
                text = cq.get("data", "")
            else:
                continue

            if cq_id:
                answer_callback(bot_token, cq_id)  # stop the button spinner
            if str(from_chat) != str(chat_id):
                continue  # only answer the owner

            # "✅ Landed" tap -> kill the buzzing and clear the landing.
            if (text or "") == LAND_ACK_DATA:
                if buzzing:
                    buzzing = False
                    send_reply(bot_token, chat_id,
                               "✅ Got it — buzzing off. Buy &amp; bounce!")
                land_arrival, land_dest, land_alerted = None, "", False
                _save_land({})
                continue

            cmd, arg = parse_command(text or "")

            # /testland (or /buzz): demo the real landing buzz on demand, ~30s,
            # so you can feel it and try the ✅ button without flying.
            if cmd == "testland":
                buzzing = True
                buzz_next = time.time()
                buzz_stop = time.time() + LAND_BUZZ_MAX
                land_dest, land_alerted = "TEST (demo)", True
                land_arrival = None
                send_reply(bot_token, chat_id,
                           f"🔔 <b>Test:</b> landing buzz for ~{LAND_BUZZ_MAX}s "
                           f"(every {LAND_BUZZ_INTERVAL}s). Tap ✅ to stop early.")
                continue


            if cmd is None:
                send_reply(bot_token, chat_id,
                           "🤷 Didn't catch that. Tap a button or try /help.")
                continue
            reply, photo = handle(cmd, arg, api_key, datetime.now(timezone.utc), tz_offset)
            if photo:
                send_photo_reply(bot_token, chat_id, photo, reply)
            elif reply:
                send_reply(bot_token, chat_id, reply)

        if updates:
            save_offset(offset)
        if args.once or time.time() >= deadline:
            break

    print("[bot] done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
