"""The ONE state file. Replaces the nine little JSON files the old two-bot
setup used (state.json, schedule_state.json, commands_state.json, …).

Layout of hans_state.json:
  v         schema version
  settings  everything changeable from Telegram (see settings.py)
  tg        offset, pinned message id/text/view, one-time flags
  bars      prev snapshot (edge-triggered alerts)
  schedule  digest_date / chart_week bookkeeping
  landing   armed landing: arrival epoch, dest, alerted
  travel    learned flights, restock cycles, depart alerts already sent
  oc        ready epoch, source (auto/manual), reminders fired

Saves are atomic (write temp file, then rename) so a crash mid-write can never
leave a half-written file that loses everything.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from . import settings as settings_mod

SCHEMA_VERSION = 1
SECTIONS = ("settings", "tg", "bars", "schedule", "landing", "travel", "oc", "invest")

# legacy file -> what it held (for migration)
LEGACY_FILES = ("state.json", "schedule_state.json", "commands_state.json",
                "pin_state.json", "travel_state.json", "depart_watch.json",
                "flights.json", "cycles.json", "oc_state.json")


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


class Store:
    def __init__(self, path: str = "hans_state.json"):
        self.path = path
        self.data: dict = {}
        self.migrated = False

    # ---- lifecycle ------------------------------------------------------
    def load(self, legacy_dir: Optional[str] = None) -> "Store":
        raw = _read_json(self.path)
        if isinstance(raw, dict):
            self.data = raw
        else:
            self.data = {}
            base = legacy_dir if legacy_dir is not None else (
                os.path.dirname(os.path.abspath(self.path)))
            self.migrated = migrate_legacy(self.data, base)
        self.data["v"] = SCHEMA_VERSION
        for s in SECTIONS:
            if not isinstance(self.data.get(s), dict):
                self.data[s] = {}
        self.data["settings"] = settings_mod.with_defaults(self.data["settings"])
        return self

    def save(self) -> None:
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, separators=(",", ":"))
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"[store] save failed: {e}")

    # ---- access ---------------------------------------------------------
    def __getitem__(self, section: str) -> dict:
        return self.data.setdefault(section, {})

    @property
    def settings(self) -> dict:
        return self.data["settings"]


def migrate_legacy(data: dict, base: str) -> bool:
    """Fold the old per-feature JSON files into one state dict. Returns True
    if anything was imported. Old files are left alone (harmless)."""
    def legacy(name):
        return _read_json(os.path.join(base, name))

    found = False
    snap = legacy("state.json")
    if isinstance(snap, dict) and snap:
        data.setdefault("bars", {})["prev"] = snap
        found = True
    sched = legacy("schedule_state.json")
    if isinstance(sched, dict) and sched:
        data["schedule"] = {k: sched[k] for k in ("digest_date", "chart_week")
                            if k in sched}
        found = True

    tg = data.setdefault("tg", {})
    off = legacy("commands_state.json")
    if isinstance(off, dict) and off.get("offset") is not None:
        tg["offset"] = off["offset"]
        found = True
    pin = legacy("pin_state.json")
    if isinstance(pin, dict) and pin.get("message_id"):
        tg["pin_id"] = pin["message_id"]
        found = True

    land = legacy("travel_state.json")
    if isinstance(land, dict) and land.get("arrival"):
        data["landing"] = {"arrival": land["arrival"], "dest": land.get("dest", ""),
                           "alerted": bool(land.get("alerted"))}
        found = True

    travel = data.setdefault("travel", {})
    flights = legacy("flights.json")
    if isinstance(flights, dict) and flights:
        travel["flights"] = {k: int(v) for k, v in flights.items() if v}
        found = True
    cycles = legacy("cycles.json")
    if isinstance(cycles, dict) and cycles:
        travel["cycles"] = cycles
        found = True

    oc = legacy("oc_state.json")
    if isinstance(oc, dict) and oc.get("ready"):
        data["oc"] = {"ready": oc["ready"], "source": oc.get("source", "auto"),
                      "fired": list(oc.get("fired", []))}
        found = True

    watch = legacy("depart_watch.json")
    if isinstance(watch, dict) and watch:
        routes = {}
        for code, cfg in watch.items():
            if isinstance(cfg, dict):
                routes[code] = {"enabled": bool(cfg.get("enabled")),
                                "item": cfg.get("item") or
                                settings_mod.DEFAULT_ROUTE_ITEMS.get(code, "")}
        data.setdefault("settings", {}).setdefault("depart", {})["routes"] = routes
        found = True
    return found
