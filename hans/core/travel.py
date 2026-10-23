"""Foreign travel: countries, stock parsing, restock projection, depart math.
All pure — the feeds are fetched in net/feeds.py."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

COUNTRIES = {
    "mex": ("Mexico", ("mexico", "mex")),
    "cay": ("Cayman Islands", ("cayman", "caymanislands", "cay", "ci")),
    "can": ("Canada", ("canada", "can")),
    "haw": ("Hawaii", ("hawaii", "haw")),
    "uni": ("United Kingdom", ("uk", "unitedkingdom", "britain", "uni")),
    "arg": ("Argentina", ("argentina", "arg")),
    "swi": ("Switzerland", ("switzerland", "swiss", "swi")),
    "jap": ("Japan", ("japan", "jap", "jp")),
    "chi": ("China", ("china", "chi", "cn")),
    "uae": ("UAE", ("uae", "dubai", "emirates", "unitedarabemirates")),
    "sou": ("South Africa", ("southafrica", "sa", "sou", "africa")),
}


def code_for(text: str) -> Optional[str]:
    t = (text or "").strip().lower().replace(" ", "")
    if t in COUNTRIES:
        return t
    for code, (_name, aliases) in COUNTRIES.items():
        if t in aliases:
            return code
    return None


def country_name(code: str) -> str:
    row = COUNTRIES.get(code)
    return row[0] if row else code


def is_foreign(destination: str) -> bool:
    """Abroad (where you buy and risk a mugging), not the trip home to Torn."""
    d = (destination or "").strip().lower()
    return bool(d) and d != "torn"


# --------------------------------------------------------------------------
# Stock feed parsing
# --------------------------------------------------------------------------
def _restock_epoch(raw) -> Optional[int]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v // 1000 if v > 10_000_000_000 else v
    s = str(raw).strip()
    if s.isdigit():
        v = int(s)
        return v // 1000 if v > 10_000_000_000 else v
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except ValueError:
        return None


def parse_stock(payload: dict) -> dict:
    """Prombot/YATA export -> {code: {"update": epoch, "items": [{id,name,qty,cost,restock}]}}"""
    out: dict = {}
    for code, block in ((payload or {}).get("stocks") or {}).items():
        block = block or {}
        raw_items = block.get("stocks") if isinstance(block, dict) else block
        items = []
        for it in (raw_items or []):
            it = it or {}
            items.append({
                "id": it.get("id"),
                "name": it.get("name") or "",
                "qty": int(it.get("quantity") or 0),
                "cost": int(it.get("cost") or 0),
                "restock": _restock_epoch(it.get("nextRestock")),
            })
        out[str(code).lower()] = {
            "update": (block.get("update") if isinstance(block, dict) else 0) or 0,
            "items": items}
    return out


def find_item(parsed: dict, code: str, needle: str) -> Optional[dict]:
    want = (needle or "").strip().lower()
    items = (parsed.get(code) or {}).get("items") or []
    if not want:
        return max(items, key=lambda x: x.get("cost") or 0) if items else None
    for it in items:
        if want == str(it.get("id")) or want in (it.get("name") or "").lower():
            return it
    return None


# --------------------------------------------------------------------------
# Restock projection + depart math
# --------------------------------------------------------------------------
def model_interval_last(model: dict, code: str, item_id) -> tuple:
    entry = (((model or {}).get("items") or {}).get(code) or {}).get(str(item_id))
    if not isinstance(entry, dict):
        return (None, None)
    return (entry.get("interval"), entry.get("last"))


def next_restock(last: int, interval: int, now: float) -> Optional[int]:
    if not last or not interval:
        return None
    if last >= now:
        return int(last)
    k = int((now - last) // interval) + 1
    return int(last + k * interval)


def depart_epoch(restock_epoch: int, flight_seconds: int, land_after_seconds: int = 120) -> int:
    """depart = restock + land_after - flight"""
    return int(restock_epoch) + int(land_after_seconds) - int(flight_seconds)


def next_catchable_depart(restock_epoch: int, flight_seconds: int, now: float,
                          cycle_seconds: Optional[int] = None,
                          land_after_seconds: int = 120) -> Optional[int]:
    """Soonest depart >= now that lands just after a restock (rolls by cycles)."""
    dep = depart_epoch(restock_epoch, flight_seconds, land_after_seconds)
    if dep >= now:
        return dep
    if not cycle_seconds:
        return None
    missed = int((now - dep) // cycle_seconds) + 1
    return dep + missed * cycle_seconds


# --------------------------------------------------------------------------
# Flight times — Torn adds ~3% random variance per flight, and the ticket type
# (standard / airstrip / private / business) changes it a lot. So we keep the
# real durations of recent trips per country+method and plan with the median.
# --------------------------------------------------------------------------
FLIGHT_VARIANCE = 0.03


def _trips(flights: dict, code: str) -> list:
    """Recent trips for a country as [[seconds, method], ...] (accepts the old
    single-number format)."""
    v = flights.get(code)
    if isinstance(v, (int, float)):
        return [[int(v), ""]]
    return [t for t in (v or []) if isinstance(t, list) and len(t) == 2]


def record_flight(flights: dict, code: str, seconds: int, method: str = "") -> None:
    """Store one completed/known trip duration (keeps the last 8)."""
    if not code or not 60 <= seconds <= 6 * 3600:
        return
    trips = _trips(flights, code) + [[int(seconds), method or ""]]
    flights[code] = trips[-8:]


def flight_estimate(flights: dict, code: str) -> Optional[dict]:
    """{"sec": median, "lo", "hi", "n"} for the most recently used ticket type."""
    trips = _trips(flights, code)
    if not trips:
        return None
    method = trips[-1][1]
    same = sorted(t[0] for t in trips if t[1] == method) or [trips[-1][0]]
    med = same[len(same) // 2]
    return {"sec": med, "lo": int(med * (1 - FLIGHT_VARIANCE)),
            "hi": int(med * (1 + FLIGHT_VARIANCE)), "n": len(same), "method": method}


# --------------------------------------------------------------------------
# Restocks — they are triggered by an item SELLING OUT (a restock follows a
# short delay after the stock hits 0), not by a fixed clock. So we watch the
# live quantity: note when it hits 0, when it comes back, how fast it sells,
# and learn each item's "empty -> restock" delay.
# --------------------------------------------------------------------------
DEFAULT_DELAY = 15 * 60          # empty -> restock, until we've measured it
MAX_DELAY = 45 * 60
MIN_DELAY = 5 * 60               # a "restock" seconds after a sell-out is feed noise


def observe_stock(track: dict, code: str, item: dict, now: float) -> Optional[dict]:
    """Feed one live reading of an item into its tracker. Returns a
    {"event": "sold_out"|"restocked", "at", "qty", "delay"} when one happened."""
    ev = None
    if not item or item.get("id") is None:
        return None
    key = f"{code}:{item['id']}"
    t = track.setdefault(key, {})
    qty, prev, prev_at = int(item.get("qty") or 0), t.get("qty"), t.get("at")
    if prev is not None and prev_at is not None:
        dt = now - prev_at
        if qty == 0 and prev > 0:
            t["zero_at"] = now - min(dt, 60) / 2        # sold out between readings
            ev = {"event": "sold_out", "at": int(t["zero_at"]), "qty": 0, "delay": ""}
        elif qty > 0 and prev == 0:
            delay = None
            if t.get("zero_at"):
                delay = now - min(dt, 60) / 2 - t["zero_at"]
                if MIN_DELAY <= delay <= MAX_DELAY:     # ignore feed blips
                    t["delays"] = (t.get("delays", []) + [int(delay)])[-10:]
            t["zero_at"] = None
            back = now - min(dt, 60) / 2
            if t.get("restocked_at") and 300 <= back - t["restocked_at"] <= 3 * 3600:
                t["gaps"] = (t.get("gaps", []) + [int(back - t["restocked_at"])])[-12:]
            t["restocked_at"] = back
            t["restock_qty"] = qty
            ev = {"event": "restocked", "at": int(back), "qty": qty,
                  "delay": int(delay) if delay and MIN_DELAY <= delay <= MAX_DELAY else ""}
        elif 0 < qty < prev and dt > 0:
            rate = (prev - qty) / dt                        # items per second
            t["rate"] = rate if not t.get("rate") else 0.7 * t["rate"] + 0.3 * rate
    # how often the shelf is empty (counts halve every ~2 days of polls)
    t["n"], t["n0"] = t.get("n", 0) + 1, t.get("n0", 0) + (qty == 0)
    if t["n"] > 2880:
        t["n"], t["n0"] = t["n"] // 2, t["n0"] // 2
    t["qty"], t["at"] = qty, now
    return ev


def shelf_at(t: dict, when: float) -> Optional[bool]:
    """Was the shelf stocked at `when` (e.g. your landing)? From the tracker's
    last sell-out / restock times; None if we can't tell."""
    if not t or t.get("qty") is None:
        return None
    ra, za = t.get("restocked_at") or 0, t.get("zero_at") or 0
    if t["qty"] > 0:
        return not (ra and ra > when)          # restocked after you landed -> was empty
    if za:
        return za > when                       # sold out after you landed -> was stocked
    return False


def restock_delay(t: dict) -> tuple:
    """(seconds, learned?) from empty to restock."""
    d = sorted((t or {}).get("delays") or [])
    return (d[len(d) // 2], True) if d else (DEFAULT_DELAY, False)


def model_sell_rate(model: dict, code: str, item_id) -> Optional[float]:
    """Community model's sell rate (items/min) -> items/sec."""
    e = (((model or {}).get("items") or {}).get(code) or {}).get(str(item_id)) or {}
    r = e.get("sellRate")
    return r / 60 if isinstance(r, (int, float)) and r > 0 else None


def predict_restock(t: dict, qty: int, model: dict, code: str, item_id,
                    now: float) -> dict:
    """When does the next restock happen, and how sure are we?
    basis: "empty"   — it's at 0 now; restock = time it emptied + delay (solid)
           "selling" — still in stock; empties at qty / sell-rate, + delay
           "model"   — no live data; community model's typical cycle (rough)"""
    t = t or {}
    delay, learned = restock_delay(t)
    rate = t.get("rate") or model_sell_rate(model, code, item_id)
    refill = t.get("restock_qty") or (((model or {}).get("items") or {})
                                      .get(code, {}).get(str(item_id), {}) or {}).get("modelQty")
    cycle = int(delay + refill / rate) if (rate and refill) else None
    if qty == 0 and t.get("zero_at"):
        return {"restock": int(t["zero_at"] + delay), "empties": int(t["zero_at"]),
                "basis": "empty", "delay": delay, "learned": learned, "cycle": cycle}
    if qty > 0 and rate:
        empties = now + qty / rate
        return {"restock": int(empties + delay), "empties": int(empties),
                "basis": "selling", "delay": delay, "learned": learned, "cycle": cycle}
    interval, last = model_interval_last(model, code, item_id)
    if interval and last:
        return {"restock": next_restock(last, interval, now), "empties": None,
                "basis": "model", "delay": delay, "learned": learned,
                "cycle": cycle or int(interval)}
    return {"restock": None, "empties": None, "basis": "none", "delay": delay,
            "learned": learned, "cycle": cycle}


# Long flights can't hit a restock on purpose, but a backtest on China Panda
# (46 restocks, real 3h42-3h56 flights) found leaving ~70% of the way through
# a cycle — i.e. ~30 min after a restock, when it's nearly sold out — did best:
# ~70% landed in stock vs ~64% leaving at random. A small edge, but the best one.
BEST_PHASE = 0.72
DEFAULT_GAP = 40 * 60


def best_window(t: dict, now: float) -> Optional[int]:
    """Next 'best odds' departure for a route too long to time exactly."""
    ra = (t or {}).get("restocked_at")
    if not ra:
        return None
    g = sorted(t.get("gaps") or [])
    gap = g[len(g) // 2] if g else DEFAULT_GAP
    best = ra + BEST_PHASE * gap
    while best < now - 60:
        best += gap
    if best - ra > 2.5 * gap:          # last restock seen too long ago — phase is stale
        return None
    return int(best)


def plan(code: str, item_needle: str, stock: dict, model: dict, flights: dict,
         track: dict, now: float, land_after: int = 120) -> Optional[dict]:
    """Everything the UI / depart watcher needs for one route, or None if the
    item isn't in the feed. `error` is how many minutes either way the depart
    time could be off (flight variance + guessing future sell-outs)."""
    it = find_item(stock, code, item_needle)
    if not it:
        return None
    t = (track or {}).get(f"{code}:{it.get('id')}") or {}
    pr = predict_restock(t, it.get("qty", 0), model, code, it.get("id"), now)
    fl = flight_estimate(flights, code)
    dep, err, too_long = None, None, False
    if pr["restock"] and fl:
        dep = depart_epoch(pr["restock"], fl["sec"], land_after)
        if dep < now:
            # Catching it would mean guessing several sell-outs ahead. Each
            # cycle drifts by minutes and the stock window is short, so any
            # clock time here is a coin flip — give none rather than mislead.
            dep, too_long = None, True
        else:
            base = {"empty": 60, "selling": 180, "model": 600}.get(pr["basis"], 600)
            err = int(fl["hi"] - fl["sec"] + base)
            if pr["basis"] == "model":           # no live sell-out to anchor on
                dep, too_long = None, False
    elif fl and pr["cycle"] and fl["sec"] > pr["cycle"]:
        too_long = True
    best = best_window(t, now) if too_long else None
    return {"code": code, "name": country_name(code), "item": it.get("name") or "",
            "id": it.get("id"), "qty": it.get("qty", 0), "cost": it.get("cost", 0),
            "restock": pr["restock"], "empties": pr["empties"], "basis": pr["basis"],
            "delay": pr["delay"], "delay_learned": pr["learned"], "cycle": pr["cycle"],
            "flight": fl["sec"] if fl else None, "flight_n": fl["n"] if fl else 0,
            "depart": dep, "too_long": too_long, "error": err, "best_depart": best,
            "empty_share": (t["n0"] / t["n"]) if t.get("n", 0) >= 30 else None,
            "zero_at": t.get("zero_at")}
