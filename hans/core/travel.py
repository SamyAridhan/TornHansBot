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


def observe_restock(cycles: dict, code: str, restock_epoch: int) -> None:
    """Learn a country's restock cycle from nextRestock jumps (5 min – 3 h)."""
    if not code or not restock_epoch:
        return
    row = cycles.setdefault(code, {})
    last = row.get("last")
    if last and restock_epoch > last and 300 <= restock_epoch - last <= 3 * 3600:
        row["cycle"] = int(restock_epoch - last)
    row["last"] = int(restock_epoch)


def learn_flight(flights: dict, code: str, seconds: int) -> bool:
    """Keep the longest one-way time seen (the trip's start = full duration)."""
    if not code or seconds <= 0 or seconds <= int(flights.get(code, 0)):
        return False
    flights[code] = int(seconds)
    return True


def restock_and_cycle(code: str, item: dict, model: dict, cycles: dict,
                      now: float) -> tuple:
    """(next_restock_epoch, cycle_seconds): model first, then feed + learned cycle."""
    interval, last = model_interval_last(model, code, item.get("id"))
    if interval and last:
        return (next_restock(last, interval, now), int(interval))
    cyc = (cycles.get(code) or {}).get("cycle")
    return (item.get("restock"), int(cyc) if cyc else None)


def plan(code: str, item_needle: str, stock: dict, model: dict, flights: dict,
         cycles: dict, now: float, land_after: int = 120) -> Optional[dict]:
    """Everything the UI/depart watcher needs for one route, or None if the
    item isn't in the feed:
      {code, name, item, qty, cost, restock, cycle, flight, depart}
    restock/depart/flight may be None when unknown."""
    it = find_item(stock, code, item_needle)
    if not it:
        return None
    rs, cyc = restock_and_cycle(code, it, model, cycles, now)
    fs = flights.get(code)
    dep = next_catchable_depart(rs, fs, now, cyc, land_after) if (rs and fs) else None
    return {"code": code, "name": country_name(code), "item": it.get("name") or "",
            "id": it.get("id"), "qty": it.get("qty", 0), "cost": it.get("cost", 0),
            "restock": rs, "cycle": cyc, "flight": int(fs) if fs else None,
            "depart": dep}
