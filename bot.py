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
             "preview", "sample", "test"}
    if cmd == "start":
        cmd = "help"
    if cmd in ("sample", "test"):
        cmd = "preview"
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
        tail = tw.fmt_duration_compact(tw.seconds_to_full(cur, mx, reg))
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
    for emoji, label, key in (("⚡", "Energy", "energy"), ("🔴", "Nerve", "nerve")):
        b = snapshot.get(key, {})
        cur, mx = b.get("current", 0), b.get("maximum", 0)
        secs = tw.seconds_to_full(cur, mx, reg.get(key, {}))
        if mx and cur >= mx:
            out.append(f"{emoji} <b>{label}</b> — already full ({cur}/{mx})")
        elif secs:
            clock = now_utc + timedelta(hours=tz_offset, seconds=secs)
            out.append(f"{emoji} <b>{label}</b> {cur}/{mx} — "
                       f"{tw.fmt_duration_compact(secs)} (≈{clock:%H:%M})")
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
    print(f"[bot] listening (offset={offset}, until +{args.seconds}s, "
          f"once={args.once}, pin={'on' if pin_on else 'off'})")

    while True:
        # Keep the pinned dashboard fresh (best-effort, never blocks commands).
        if pin_on and time.time() - last_pin >= pin_every:
            try:
                refresh_pin(api_key, bot_token, chat_id, tz_offset)
            except Exception as e:  # noqa: BLE001
                print(f"[bot] pin refresh error (non-fatal): {e}", file=sys.stderr)
            last_pin = time.time()

        try:
            updates = get_updates(bot_token, offset, timeout=0 if args.once else 30)
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

            cmd, arg = parse_command(text or "")
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
