"""'Leave now' alerts: for each watched country, fire at the exact moment to
take off so you land ~2 min after a restock."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
from .. import settings as S
from ..core import bars
from ..core import travel
from ..core.fmt import clock, html_escape

GRACE = 30   # still fire if we wake up to this many seconds late


REPEAT_GAP = 15 * 60   # don't re-ping the same country within this window


def track_stock(ctx) -> None:
    """Record live quantities of every route's item (all 11 countries, one
    feed call) so restock delays and sell rates are learned continuously."""
    stock = ctx.feeds.stock()
    if not stock:
        return
    now = ctx.now()
    track = ctx.store["travel"].setdefault("stock", {})
    for code, r in ctx.settings["depart"]["routes"].items():
        item = travel.find_item(stock, code, (r or {}).get("item", ""))
        if item:
            travel.observe_stock(track, code, item, now)


def run(ctx) -> float:
    track_stock(ctx)
    if not ctx.settings["depart"]["on"]:
        return C.STOCK_POLL
    snap = ctx.snapshot or {}
    if snap.get("travel", {}).get("time_left", 0) > 0 or ctx.store["landing"].get("arrival"):
        return C.STOCK_POLL            # already flying — nothing to leave for
    now = ctx.now()
    plans, ok = ctx.plans(now=now - GRACE)
    if not ok:
        return C.STOCK_POLL
    fired = ctx.store["travel"].setdefault("depart_fired", {})
    quiet = bars.in_quiet_hours(datetime.fromtimestamp(now, timezone.utc),
                                S.alert_config(ctx.settings), ctx.tz)
    waits = [C.STOCK_POLL]
    for p in plans:
        dep = p.get("depart") or p.get("best_depart")
        if not dep:
            continue
        if dep <= now + 5:
            if not p.get("depart") and quiet:
                continue                 # best-odds hints are optional; respect quiet hours
            if now - float(fired.get(p["code"]) or 0) > REPEAT_GAP:
                fired[p["code"]] = now
                ctx.send(depart_text(p, ctx.tz),
                         {"inline_keyboard": [[{"text": f"📦 {p['name']} details",
                                                "callback_data": f"new:stk:{p['code']}"}]]})
        else:
            waits.append(dep - now)
    return max(5, min(waits))


def depart_text(p: dict, tz: int) -> str:
    if not p.get("depart"):
        return (f"🛫 <b>Best time to leave for {p['name']}: now</b>\n"
                f"{html_escape(p['item'])} restocked ~30 min ago and is nearly sold out — "
                f"leaving now gave the best odds in testing (~70% land in stock, not a sure "
                f"thing). I'll show the live shelf 1 min before you land.")
    land = p["depart"] + (p.get("flight") or 0)
    if p["basis"] == "empty":
        why = f"it sold out at {clock(p['empties'], tz)}"
    elif p["basis"] == "selling" and p.get("empties"):
        why = f"it should sell out ~{clock(p['empties'], tz)}"
    else:
        why = "community restock estimate"
    err = p.get("error") or 0
    sure = "" if err < 240 else f"\n<i>Rough estimate (±{err // 60} min) — long flight.</i>"
    return (f"🛫 <b>Leave for {p['name']} now!</b>\n"
            f"{html_escape(p['item'])}: you'll land ~{clock(land, tz)}, about 2 min after "
            f"the restock ({why}).{sure}")
