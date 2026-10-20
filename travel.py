#!/usr/bin/env python3
"""
Travel stock + depart-timing helper for Torn Watch.

Live foreign-stock and next-restock data come from the community feeds
Prombot (primary) and YATA (fallback) — the same free, public sources weav3r
uses. No Torn API key is needed for stock; it's open data.

Design mirrors torn_watch/bot: network I/O is injectable, the math is pure, so
everything here is unit-tested without touching the network.
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from typing import Callable, Optional

PROMBOT_URL = "https://api.prombot.co.uk/api/travel"
YATA_URL = "https://yata.yt/api/v1/travel/export/"

# Short country codes used by the Prombot/YATA export, mapped to nice names and
# the aliases you'd actually type to Hans (/stock china, /watch mex, …).
COUNTRIES = {
    "mex": ("Mexico", ("mexico", "mex")),
    "cay": ("Cayman Islands", ("cayman", "cay", "ci")),
    "can": ("Canada", ("canada", "can")),
    "haw": ("Hawaii", ("hawaii", "haw")),
    "uni": ("United Kingdom", ("uk", "unitedkingdom", "britain", "uni")),
    "arg": ("Argentina", ("argentina", "arg")),
    "swi": ("Switzerland", ("switzerland", "swiss", "swi")),
    "jap": ("Japan", ("japan", "jap", "jp")),
    "chi": ("China", ("china", "chi", "cn")),
    "uae": ("UAE", ("uae", "dubai", "emirates")),
    "sou": ("South Africa", ("southafrica", "sa", "sou", "africa")),
}


def code_for(text: str) -> Optional[str]:
    """Resolve a typed country word ('china', 'mex') to its export code."""
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


# --------------------------------------------------------------------------
# Parsing (pure)
# --------------------------------------------------------------------------
def _restock_epoch(raw) -> Optional[int]:
    """nextRestock may be a unix int, a numeric string, or an ISO timestamp.
    Return epoch seconds, or None if absent/unparseable."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        v = int(raw)
        return v // 1000 if v > 10_000_000_000 else v  # tolerate milliseconds
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
    """Normalise a Prombot/YATA export into:
        { code: { "update": <epoch>, "items": [ {id,name,qty,cost,restock} ] } }
    Defensive: tolerates missing keys and either {stocks:[...]} or a bare list.
    """
    out: dict = {}
    stocks = (payload or {}).get("stocks") or {}
    for code, block in stocks.items():
        block = block or {}
        raw_items = block.get("stocks")
        if raw_items is None and isinstance(block, list):
            raw_items = block
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
        out[str(code).lower()] = {"update": block.get("update") or 0, "items": items}
    return out


def find_item(parsed: dict, code: str, needle: str) -> Optional[dict]:
    """First item in a country whose name/id matches `needle` (substring, ci)."""
    want = (needle or "").strip().lower()
    block = parsed.get(code) or {}
    items = block.get("items") or []
    if not want:
        # no filter -> the highest-value plushie/flower-ish pick (max cost)
        return max(items, key=lambda x: x.get("cost") or 0) if items else None
    for it in items:
        if want == str(it.get("id")) or want in (it.get("name") or "").lower():
            return it
    return None


# --------------------------------------------------------------------------
# Depart-time math (pure)
# --------------------------------------------------------------------------
def depart_epoch(restock_epoch: int, flight_seconds: int,
                 land_after_seconds: int = 120) -> int:
    """When to take off so you LAND `land_after_seconds` after a restock.

    depart = restock + land_after - flight. Returns epoch seconds (may be in the
    past if that restock is already too close to catch — caller rolls forward)."""
    return int(restock_epoch) + int(land_after_seconds) - int(flight_seconds)


def next_catchable_depart(restock_epoch: int, flight_seconds: int, now: float,
                          cycle_seconds: Optional[int] = None,
                          land_after_seconds: int = 120) -> Optional[int]:
    """Soonest future depart time that lands just after a restock.

    Uses the known next restock; if that departure has already passed and we
    know the restock cycle length, rolls forward by whole cycles. Without a
    cycle it returns None when the next restock is unreachable."""
    dep = depart_epoch(restock_epoch, flight_seconds, land_after_seconds)
    if dep >= now:
        return dep
    if not cycle_seconds:
        return None
    # advance the restock by whole cycles until the depart is in the future
    missed = int((now - dep) // cycle_seconds) + 1
    return dep + missed * cycle_seconds


# --------------------------------------------------------------------------
# Network (injectable)
# --------------------------------------------------------------------------
def _get_json(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "torn-watch/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_stock(getter: Callable[[str], dict] = _get_json) -> dict:
    """Prombot first, YATA fallback. Returns parse_stock(...) or {} on failure."""
    for url in (PROMBOT_URL, YATA_URL):
        try:
            return parse_stock(getter(url))
        except Exception:  # noqa: BLE001 — try the fallback, then give up
            continue
    return {}
