"""Answers everything you send: button taps (edit the message in place) and
typed commands (reply with a new message). Read-only, like the rest of Hans."""

from __future__ import annotations

from typing import Optional

from .. import config as C
from .. import settings as S
from ..core import bars, oc as ocm, stats, travel
from ..core.messages import format_message, preview_samples
from ..jobs import landing as landing_job, oc as oc_job, schedule
from ..net.torn import TornApiError
from . import keyboards as K, views as V

COMMANDS = [
    ("menu", "Home screen with all buttons"),
    ("status", "Bars, cooldowns, travel right now"),
    ("travel", "Stock, restock & when to leave"),
    ("stock", "One country: /stock china"),
    ("oc", "OC time & reminders: /oc in 12h"),
    ("stats", "Battle stats"),
    ("settings", "Turn alerts on/off"),
    ("help", "Quick guide"),
]

_ALIASES = {"start": "menu", "home": "menu", "status": "menu", "next": "next",
            "buzz": "testland", "sample": "preview", "test": "preview",
            "set": "settings", "config": "settings"}
_KNOWN = {"menu", "next", "travel", "stock", "oc", "stats", "settings", "help",
          "price", "watch", "testland", "preview"}
# old inline buttons still on earlier messages
_LEGACY_DATA = {"status": "nav:home", "stats": "nav:stats", "next": "nav:next",
                "help": "nav:help", "ack_landing": "ack"}


def parse_command(text: str) -> tuple[Optional[str], str]:
    text = (text or "").strip()
    if not text:
        return None, ""
    if text.startswith("/"):
        text = text[1:]
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower().split("@", 1)[0]
    cmd = _ALIASES.get(cmd, cmd)
    if cmd not in _KNOWN:
        return None, ""
    return cmd, (parts[1].strip() if len(parts) > 1 else "")


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------
def render(ctx, screen: str) -> tuple[str, dict]:
    now, tz = ctx.now(), ctx.tz
    if screen == "home":
        return V.status(ctx.fresh_snapshot(), now, tz, ctx.home_extras()), K.HOME
    if screen == "next":
        return V.next_full(ctx.fresh_snapshot(), now, tz), K.back()
    if screen == "help":
        return V.HELP, K.back()
    if screen == "travel":
        plans, ok = ctx.plans()
        return (V.travel_screen(plans, ctx.settings, ctx.snapshot, now, tz, ok),
                K.travel_menu(ctx.settings["depart"]["routes"]))
    if screen.startswith("stk:"):
        code = screen[4:]
        plans, _ok = ctx.plans([code])
        enabled = bool((ctx.settings["depart"]["routes"].get(code) or {}).get("enabled"))
        return (V.route_detail(plans[0] if plans else None, code, enabled, now, tz,
                               C.LAND_AFTER_RESTOCK), K.route(code, enabled))
    if screen == "oc":
        return V.oc_screen(ctx.store["oc"], ctx.settings, now, tz), K.oc()
    if screen == "stats":
        st = bars.parse_battlestats(ctx.torn.user("battlestats"))
        rows = stats.read_history(ctx.cfg.history_path)
        return V.stats(st, rows[-1] if rows else None), K.stats()
    if screen == "set":
        return V.settings_home(), K.settings_home()
    if screen.startswith("set:") and screen[4:] in S.PAGES:
        page = screen[4:]
        return V.settings_page(page), K.settings_page(page, ctx.settings)
    return render(ctx, "home")


def _safe_render(ctx, screen: str) -> tuple[str, dict]:
    try:
        return render(ctx, screen)
    except TornApiError as e:
        return f"⚠️ Torn API said: {e.message}", K.back()
    except Exception as e:  # noqa: BLE001
        ctx.log(f"render {screen}: {e}")
        return "⚠️ Couldn't load that right now — try again in a moment.", K.back()


def show(ctx, screen: str, message_id: Optional[int] = None) -> None:
    """Edit `message_id` into `screen`, or send it as a new message."""
    text, markup = _safe_render(ctx, screen)
    if message_id:
        if ctx.tg.edit(message_id, text, markup).get("ok"):
            tg = ctx.store["tg"]
            if message_id == tg.get("pin_id"):
                tg["pin_view"], tg["pin_view_at"] = screen, ctx.now()
                if screen == "home":
                    tg["pin_text"] = None
            return
    ctx.send(text, markup)


# --------------------------------------------------------------------------
# Button taps
# --------------------------------------------------------------------------
def on_callback(ctx, data: str, message_id: Optional[int]) -> str:
    """Handle one tap. Returns a short toast for answerCallbackQuery ('' = none)."""
    data = _LEGACY_DATA.get(data, data)
    if data.startswith("new:"):          # buttons on alerts: open a fresh message
        show(ctx, data[4:].replace("nav:", "", 1))
        return ""
    if data == "ack":
        if ctx.buzz:
            landing_job.stop(ctx, "ack")
        else:
            ctx.store["landing"].clear()
        return "Buzz off ✅"
    kind, _, rest = data.partition(":")
    if kind == "nav":
        show(ctx, rest or "home", message_id)
        return ""
    if kind == "set":
        key, _, page = rest.partition(":")
        S.apply(ctx.settings, key)
        if key in ("ll", "lb"):
            ctx.wake("landing")
        ctx.wake("pin")
        show(ctx, f"set:{page}" if page in S.PAGES else "set", message_id)
        return "Saved"
    if kind == "stk":
        show(ctx, f"stk:{rest}", message_id)
        return ""
    if kind == "w":
        on = ctx.toggle_route(rest)
        show(ctx, f"stk:{rest}", message_id)
        return "Alert on 🔔" if on else "Alert off"
    if kind == "oc":
        if rest.startswith("in:"):
            oc_job.set_manual(ctx, int(ctx.now() + float(rest[3:]) * 3600))
            toast = "OC time set"
        elif rest == "auto":
            ctx.store["oc"].clear()
            toast = "Read from faction ✅" if oc_job.auto_read(ctx) else \
                "Couldn't read it from the faction — set it by hand"
        else:
            ctx.store["oc"].clear()
            toast = "Cleared"
        show(ctx, "oc", message_id)
        return toast
    if kind == "run":
        return run_action(ctx, rest)
    return ""


def run_action(ctx, what: str) -> str:
    try:
        if what == "digest":
            schedule.send_digest(ctx)
            return "Digest sent"
        if what == "chart":
            schedule.send_chart(ctx)
            return "Chart sent"
        if what == "buzz":
            start_demo(ctx)
            return "Test buzz…"
    except Exception as e:  # noqa: BLE001
        ctx.log(f"run {what}: {e}")
        return "That failed — try again later"
    return ""


def start_demo(ctx) -> None:
    now = ctx.now()
    half = C.BUZZ_DEMO_SECONDS // 2
    landing_job.start(ctx, "TEST", now + half, now + C.BUZZ_DEMO_SECONDS, demo=True)


# --------------------------------------------------------------------------
# Typed commands
# --------------------------------------------------------------------------
def on_command(ctx, cmd: Optional[str], arg: str) -> None:
    if cmd is None:
        ctx.send("🤷 Didn't catch that — here's the menu.", K.HOME)
        return
    if cmd == "menu":
        show(ctx, "home")
    elif cmd in ("next", "travel", "stats", "help"):
        show(ctx, cmd)
    elif cmd == "settings":
        show(ctx, "set")
    elif cmd == "stock":
        _stock(ctx, arg)
    elif cmd == "watch":
        parts = arg.split()
        code = travel.code_for(parts[0]) if parts else None
        if not code:
            show(ctx, "travel")
            return
        r = ctx.settings["depart"]["routes"].get(code) or {}
        want = parts[1].lower() if len(parts) > 1 else None
        if want not in ("on", "off") or (want == "on") != bool(r.get("enabled")):
            ctx.toggle_route(code)
        show(ctx, f"stk:{code}")
    elif cmd == "oc":
        _oc(ctx, arg)
    elif cmd == "price":
        _price(ctx, arg)
    elif cmd == "testland":
        start_demo(ctx)
        ctx.send(f"🔔 <b>Test:</b> landing buzz for ~{C.BUZZ_DEMO_SECONDS}s. "
                 f"Tap ✅ to stop early.")
    elif cmd == "preview":
        ctx.send("👁 <b>Alert preview</b> <i>(samples)</i>\n\n"
                 + format_message(preview_samples(ctx.now()), ctx.now()))


def _stock(ctx, arg: str) -> None:
    parts = arg.split()
    if not parts:
        show(ctx, "travel")
        return
    code = travel.code_for(parts[0])
    if not code:
        ctx.send(f"🤷 Don't know '{parts[0]}'. Try china, mexico, uae…",
                 K.travel_menu(ctx.settings["depart"]["routes"]))
        return
    if len(parts) == 1:
        show(ctx, f"stk:{code}")
        return
    plans, _ok = ctx.plans([code], needle=" ".join(parts[1:]))
    enabled = bool((ctx.settings["depart"]["routes"].get(code) or {}).get("enabled"))
    ctx.send(V.route_detail(plans[0] if plans else None, code, enabled, ctx.now(), ctx.tz),
             K.route(code, enabled))


def _oc(ctx, arg: str) -> None:
    a = arg.strip().lower()
    if a in ("off", "clear", "stop"):
        ctx.store["oc"].clear()
    elif a:
        when = ocm.parse_when(arg, ctx.now(), ctx.tz)
        if not when:
            ctx.send("🤷 Couldn't read that time. Try <code>/oc in 12h</code> or "
                     "<code>/oc 21/10 14:00</code>.", K.oc())
            return
        oc_job.set_manual(ctx, when)
    show(ctx, "oc")


def _price(ctx, arg: str) -> None:
    item_id = arg.strip()
    if not item_id.isdigit():
        ctx.send("💰 Give me an item id, e.g. <code>/price 206</code> "
                 "(it's in the item's market URL).")
        return
    try:
        raw = ctx.torn.market(item_id)
    except TornApiError as e:
        ctx.send(f"⚠️ Torn API said: {e.message}")
        return
    except Exception as e:  # noqa: BLE001
        ctx.log(f"price: {e}")
        ctx.send("⚠️ Couldn't reach the market right now.")
        return
    low = stats.parse_market(raw)
    meta = stats.parse_market_item(raw)
    caption = V.price(item_id, meta["name"], low)
    img = meta["image"] or f"https://www.torn.com/images/items/{item_id}/large.png"
    if low is None or not ctx.tg.photo_url(img, caption):
        ctx.send(caption)


# --------------------------------------------------------------------------
# Entry: one Telegram update
# --------------------------------------------------------------------------
def handle_update(ctx, u: dict) -> None:
    if "callback_query" in u:
        cq = u["callback_query"]
        msg = cq.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != ctx.tg.chat_id:
            ctx.tg.answer(cq.get("id"))
            return
        toast = on_callback(ctx, cq.get("data") or "", msg.get("message_id"))
        ctx.tg.answer(cq.get("id"), toast)
    elif "message" in u:
        m = u["message"]
        if str((m.get("chat") or {}).get("id")) != ctx.tg.chat_id:
            return
        on_command(ctx, *parse_command(m.get("text") or ""))
