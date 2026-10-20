"""'Leave now' alerts: for each watched country, fire at the exact moment to
take off so you land ~2 min after a restock."""

from __future__ import annotations

from .. import config as C
from ..core import travel
from ..core.fmt import clock, html_escape

GRACE = 30   # still fire if we wake up to this many seconds late


def run(ctx) -> float:
    if not ctx.settings["depart"]["on"]:
        return 300
    snap = ctx.snapshot or {}
    if snap.get("travel", {}).get("time_left", 0) > 0 or ctx.store["landing"].get("arrival"):
        return 120                     # already flying — nothing to leave for
    now = ctx.now()
    plans, ok = ctx.plans(now=now - GRACE)
    if not ok:
        return C.DEPART_POLL
    t = ctx.store["travel"]
    fired = t.setdefault("depart_fired", {})
    stock = ctx.feeds.stock()
    waits = [C.DEPART_POLL]
    for p in plans:
        feed_item = travel.find_item(stock, p["code"], p["item"]) or {}
        travel.observe_restock(t.setdefault("cycles", {}), p["code"], feed_item.get("restock"))
        dep = p.get("depart")
        if not dep:
            continue
        if dep <= now + 5:
            if fired.get(p["code"]) != dep:
                fired[p["code"]] = dep
                land = dep + (p.get("flight") or 0)
                ctx.send(f"🛫 <b>Leave for {p['name']} now!</b>\n"
                         f"{html_escape(p['item'])} · you'll land {clock(land, ctx.tz)}, "
                         f"~2 min after the restock · {p['qty']:,} in stock now.",
                         {"inline_keyboard": [[{"text": f"📦 {p['name']} details",
                                                "callback_data": f"new:stk:{p['code']}"}]]})
        else:
            waits.append(dep - now)
    return max(5, min(waits))
