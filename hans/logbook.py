"""Plain CSV logs committed back to the repo at the end of every run, so the
history survives forever and can be analysed later (what worked, what didn't).

  logs/trips.csv     one row per foreign trip: when you left/landed, flight
                     length, whether you left on a Hans hint, shelf at landing
  logs/restocks.csv  every sell-out / restock Hans saw for your route items
"""

from __future__ import annotations

import csv
import os
from datetime import datetime, timezone

TRIPS = ("logs/trips.csv", ["departed_utc", "landed_utc", "country", "item", "method",
                            "flight_s", "hint", "hint_kind", "shelf_at_landing",
                            "qty_after", "restock_eta_utc"])
RESTOCKS = ("logs/restocks.csv", ["at_utc", "country", "item", "event", "qty",
                                  "empty_to_restock_s"])


def iso(epoch) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat(timespec="seconds")


def append(spec: tuple, row: dict, base: str = ".") -> None:
    path, header = spec
    path = os.path.join(base, path)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)
    except OSError as e:
        print(f"[logbook] {path}: {e}")


def read(spec: tuple, base: str = ".") -> list[dict]:
    try:
        with open(os.path.join(base, spec[0]), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []
