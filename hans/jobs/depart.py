"""'Leave now' alerts: for each watched country, fire at the exact moment to
take off so you land ~2 min after a restock."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
from .. import logbook
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
            ev = travel.observe_stock(track, code, item, now)
            if ev:
                logbook.append(logbook.RESTOCKS, {
                    "at_utc": logbook.iso(ev["at"]), "country": code, "item": item["name"],
                    "event": ev["event"], "qty": ev["qty"],
                    "empty_to_restock_s": ev["delay"]}, ctx.cfg.log_dir)


def watch_abroad(ctx) -> None:
    """You landed abroad to an empty shelf (and maybe parked in hospital to
    stay safe): ping the moment your item is back in stock."""
    tv = (ctx.snapshot or {}).get("travel") or {}
    code = travel.code_for(tv.get("destination", ""))
    st = ctx.store["travel"]
    if not code or tv.get("time_left", 0) > 0:
        st.pop("abroad_watch", None)               # home, or still in the air
        return
    w = st.get("abroad_watch")
    if not w or w.get("code") != code:
        w = st["abroad_watch"] = {"code": code, "empty": False, "pinged": False}
    route = ctx.settings["depart"]["routes"].get(code) or {}
    it = travel.find_item(ctx.feeds.stock() or {}, code,
                          route.get("item") or S.DEFAULT_ROUTE_ITEMS.get(code, ""))
    if not it:
        return
    if it.get("qty", 0) <= 0:
        w["empty"] = True
    elif w["empty"] and not w["pinged"] and not ctx.asleep():
        w["pinged"] = True
        ctx.send(f"🟢 <b>{html_escape(it['name'])} back in stock in "
                 f"{travel.country_name(code)}!</b> {it['qty']:,} on the shelf — buy now "
                 f"and fly home.")


def run(ctx) -> float:
    track_stock(ctx)
    watch_abroad(ctx)
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
            if not p.get("depart") and quiet and not ctx.user_active():
                continue                 # asleep: best-odds hints are optional, stay quiet
            if now - float(fired.get(p["code"]) or 0) > REPEAT_GAP:
                fired[p["code"]] = now
                ctx.store["travel"].setdefault("hints", {})[p["code"]] = {
                    "at": int(now), "kind": "exact" if p.get("depart") else "best_odds"}
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
                f"a good time to go (~60–65% land in stock — a 4h flight can't be timed "
                f"exactly). If it's empty when you land, it restocks in ~15 min: park in "
                f"hospital and I'll ping you the moment it's back.")
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
