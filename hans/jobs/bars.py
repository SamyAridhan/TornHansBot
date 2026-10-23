"""Bars job: one Torn fetch feeds everything — bar/cooldown/hospital alerts,
the dashboard snapshot, flight-time learning, and arming the landing buzz."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
from .. import logbook
from .. import settings as S
from ..core import bars, travel
from ..core.messages import format_message


def run(ctx) -> float:
    now = ctx.now()
    snap = bars.parse_snapshot(ctx.torn.user())
    ctx.snapshot, ctx.snapshot_at = snap, now

    cfg = S.alert_config(ctx.settings)
    store_bars = ctx.store["bars"]
    events = bars.evaluate(store_bars.get("prev"), snap, cfg)
    store_bars["prev"] = snap
    to_send = bars.filter_quiet(events, datetime.fromtimestamp(now, timezone.utc), cfg, ctx.tz)
    if to_send:
        ctx.send(format_message(to_send, now))
        ctx.wake("pin")

    track_travel(ctx, snap, now)
    return bars.next_sleep_seconds(snap, cfg, cap=C.BARS_POLL_MAX, min_sleep=C.BARS_POLL_MIN)


def track_travel(ctx, snap: dict, now: float) -> None:
    """Learn real flight times and (re)arm / clear the pending landing.

    Landing time = now + time_left (exact to the second)."""
    tv = snap.get("travel", {})
    tl, dest = tv.get("time_left", 0), tv.get("destination") or ""
    land = ctx.store["landing"]
    learn_trip(ctx, tv, now)
    if tl > 0 and travel.is_foreign(dest):
        arrival = now + tl                # proven; the stamps only refine flight length
        if (not land.get("arrival") or land.get("dest") != dest
                or (abs(float(land["arrival"]) - arrival) > 8 and not land.get("alerted"))):
            land.clear()
            land.update({"arrival": arrival, "dest": dest, "alerted": False})
            ctx.wake("landing")
        return
    if tl > 0:
        land.clear()                      # heading home: no buzz for Torn...
        home = ctx.store["travel"].setdefault("home", {})
        if not home.get("arrival") or abs(home["arrival"] - (now + tl)) > 8:
            home.clear()                  # ...just one simple "back in Torn" ping
            home["arrival"] = now + tl
            ctx.wake("landing")
        return
    if land.get("arrival") and (land.get("alerted") or now > float(land["arrival"]) + 20):
        land.clear()                      # landed (already buzzed) or stale


def learn_trip(ctx, tv: dict, now: float) -> None:
    """Measure each outbound flight. Exact when Torn gives departed + arrival
    stamps; otherwise the longest time_left seen. Saved when the trip ends."""
    t = ctx.store["travel"]
    trip = t.get("trip") or {}
    tl, dest = tv.get("time_left", 0), tv.get("destination") or ""
    if tl > 0 and travel.is_foreign(dest):
        if trip.get("dest") != dest:
            trip = {"dest": dest}
        exact = tv.get("arrival", 0) - tv.get("departed", 0)
        # trust the stamps only if they agree with time_left (field meaning unverified)
        sane = tv.get("departed") and exact >= tl and abs(tv.get("arrival", 0) - (now + tl)) < 120
        if sane:
            trip.update(flight=exact, exact=True)
        elif not trip.get("exact"):
            trip["flight"] = max(int(trip.get("flight", 0)), tl)
        trip["method"] = tv.get("method", "")
        trip["arrival"] = int(now + tl)
        t["trip"] = trip
    elif trip:
        code = travel.code_for(trip["dest"]) or ""
        if trip.get("flight"):
            travel.record_flight(t.setdefault("flights", {}), code,
                                 int(trip["flight"]), trip.get("method", ""))
        log_trip(ctx, trip, code)
        t.pop("trip", None)


def log_trip(ctx, trip: dict, code: str) -> None:
    """Write the finished trip to logs/trips.csv and keep the last 30 in state
    (Travel → 📒 Trip log), so we can see what actually works."""
    t = ctx.store["travel"]
    arrival = trip.get("arrival") or 0
    departed = arrival - int(trip.get("flight") or 0)
    hint = (t.get("hints") or {}).get(code) or {}
    followed = bool(hint) and -120 <= departed - hint.get("at", 0) <= 900
    route = (ctx.settings["depart"]["routes"].get(code) or {})
    item, shelf, qty, eta = route.get("item", ""), None, "", None
    try:
        plans, _ok = ctx.plans([code])
        if plans:
            p = plans[0]
            item, qty = p["item"], p["qty"]
            tr = t.get("stock", {}).get(f"{code}:{p['id']}") or {}
            shelf = travel.shelf_at(tr, arrival)
            za = tr.get("zero_at") or 0
            if shelf and 0 <= za - arrival <= 120:
                shelf = "just_missed"           # sold out within 2 min of landing
            eta = p.get("restock") if shelf is False else None
    except Exception as e:  # noqa: BLE001
        ctx.log(f"trip log: {e}")
    row = {"departed_utc": logbook.iso(departed), "landed_utc": logbook.iso(arrival),
           "country": code, "item": item, "method": trip.get("method", ""),
           "flight_s": int(trip.get("flight") or 0),
           "hint": "yes" if followed else "no",
           "hint_kind": hint.get("kind", "") if followed else "",
           "shelf_at_landing": {True: "in_stock", False: "empty",
                                "just_missed": "just_missed"}.get(shelf, "unknown"),
           "qty_after": qty, "restock_eta_utc": logbook.iso(eta)}
    logbook.append(logbook.TRIPS, row, ctx.cfg.log_dir)
    t["trips"] = (t.get("trips", []) + [{"land": arrival, "code": code, "hint": followed,
                                         "kind": row["hint_kind"],
                                         "shelf": row["shelf_at_landing"]}])[-30:]
