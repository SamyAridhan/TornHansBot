#!/usr/bin/env python3
"""
Torn Watch — Tier 2 modules.

Three additions that bolt onto the Tier 1 poller, all read-only:

  1. Market-price alerts  — ping when a watched item's cheapest listing drops
                            to/below a target price (deal spotting).
  2. Open-OC-slot alerts  — ping when your faction opens an OC slot where your
                            Checkpoint Pass Rate is high enough to be worth taking.
  3. Stats-over-time log  — append your battle stats + NNB to a CSV every poll,
                            so you can chart growth later.

As with Tier 1, all logic is edge-triggered and the network is injected, so this
is unit-tested without touching the API. The exact live API field names for the
market and faction-OC endpoints are confirmed on the first real run (they differ
by API version); parsing is defensive so a mismatch yields no false alerts, never
a crash.
"""

from __future__ import annotations

import csv
import os
from typing import Optional


# --------------------------------------------------------------------------
# 1. Market-price alerts
# --------------------------------------------------------------------------
def parse_market(raw: dict) -> Optional[int]:
    """Return the lowest listing price from a Torn market payload, or None.

    Handles a couple of shapes defensively:
      - {"itemmarket": {"listings": [{"price": N, ...}, ...]}}
      - {"itemmarket": [{"cost": N, ...}, ...]}   (older shape)
    Any listing key among price/cost/value is accepted.
    """
    im = raw.get("itemmarket", raw.get("bazaar"))
    if im is None:
        return None
    listings = im.get("listings") if isinstance(im, dict) else im
    if not isinstance(listings, list) or not listings:
        return None
    prices = []
    for entry in listings:
        if not isinstance(entry, dict):
            continue
        for key in ("price", "cost", "value"):
            if key in entry and entry[key] is not None:
                try:
                    prices.append(int(entry[key]))
                    break
                except (TypeError, ValueError):
                    pass
    return min(prices) if prices else None


def evaluate_market(prev_lows: dict, cur_lows: dict, watchlist: dict) -> tuple[list[dict], dict]:
    """Edge-triggered deal alerts.

    watchlist: {item_id(str): {"name": str, "target": int}}
    prev_lows / cur_lows: {item_id(str): lowest_price(int)}
    Fires when an item's lowest crosses DOWN to/below its target
    (was above target or unknown, now at/below).
    Returns (events, new_state) where new_state = cur_lows.
    """
    events = []
    for item_id, cfg in watchlist.items():
        target = cfg.get("target")
        cur = cur_lows.get(item_id)
        if target is None or cur is None:
            continue
        prev = prev_lows.get(item_id)
        was_deal = prev is not None and prev <= target
        now_deal = cur <= target
        if now_deal and not was_deal:
            events.append({
                "type": "market_deal",
                "item_id": item_id,
                "name": cfg.get("name", item_id),
                "price": cur,
                "target": target,
            })
    return events, dict(cur_lows)


# --------------------------------------------------------------------------
# 2. Open-OC-slot alerts
# --------------------------------------------------------------------------
def evaluate_oc(prev_open: list, cur_slots: list, min_cpr: int) -> tuple[list[dict], list]:
    """Edge-triggered open-slot alerts.

    cur_slots: [{"scenario": str, "role": str, "cpr": int, "open": bool}, ...]
    prev_open: list of "scenario|role" keys that were already open last poll.
    Fires for a slot that is open now, has cpr >= min_cpr, and was NOT open before.
    Returns (events, new_state) where new_state = list of currently-open keys.
    """
    prev_set = set(prev_open or [])
    events = []
    cur_open_keys = []
    for slot in cur_slots:
        if not slot.get("open"):
            continue
        key = f"{slot.get('scenario','?')}|{slot.get('role','?')}"
        cur_open_keys.append(key)
        if slot.get("cpr", 0) >= min_cpr and key not in prev_set:
            events.append({
                "type": "oc_slot",
                "scenario": slot.get("scenario", "?"),
                "role": slot.get("role", "?"),
                "cpr": slot.get("cpr", 0),
            })
    return events, cur_open_keys


# --------------------------------------------------------------------------
# 3. Stats-over-time log
# --------------------------------------------------------------------------
HISTORY_HEADER = ["timestamp", "strength", "defense", "speed", "dexterity", "total", "nnb"]


def parse_battlestats(raw: dict) -> dict:
    """Pull battle stats from a 'battlestats' payload, defensively."""
    def g(k):
        try:
            return int(raw.get(k))
        except (TypeError, ValueError):
            return 0
    return {
        "strength": g("strength"),
        "defense": g("defense"),
        "speed": g("speed"),
        "dexterity": g("dexterity"),
        "total": g("total"),
    }


def append_history(path: str, timestamp: str, stats: dict, nnb: int) -> None:
    """Append one row to the history CSV, writing the header if new."""
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HISTORY_HEADER)
        w.writerow([
            timestamp,
            stats.get("strength", 0),
            stats.get("defense", 0),
            stats.get("speed", 0),
            stats.get("dexterity", 0),
            stats.get("total", 0),
            nnb,
        ])


# --------------------------------------------------------------------------
# Message formatting for Tier 2 events
# --------------------------------------------------------------------------
def format_tier2_event(ev: dict) -> str:
    t = ev["type"]
    if t == "market_deal":
        return (f"🛒 Deal: {ev['name']} listed at ${ev['price']:,} "
                f"(target ${ev['target']:,}).")
    if t == "oc_slot":
        return (f"🎯 OC slot open: {ev['scenario']} — {ev['role']} "
                f"(your CPR {ev['cpr']}%).")
    return f"Notice: {t}"
