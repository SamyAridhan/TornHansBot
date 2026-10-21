"""Shared runtime context handed to every job and to the UI router: the clients,
the state store, the latest snapshot, and small helpers."""

from __future__ import annotations

import sys
import time
from typing import Callable, Optional

from . import config as C
from . import settings as S
from .config import Config
from .core import bars, travel
from .core.fmt import clock, fmt_duration_compact as dur
from .net.feeds import Feeds
from .net.telegram import Telegram
from .net.torn import Torn
from .store import Store


class Ctx:
    def __init__(self, cfg: Config, store: Store, tg: Telegram, torn: Torn,
                 feeds: Feeds, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.store = store
        self.tg = tg
        self.torn = torn
        self.feeds = feeds
        self.clock = clock
        self.snapshot: Optional[dict] = None
        self.snapshot_at = 0.0
        self.buzz: Optional[dict] = None      # live landing buzz (runtime only)
        self.jobs: dict = {}                  # name -> [fn, due]
        self.flags: dict = {}

    # ---- basics ---------------------------------------------------------
    def now(self) -> float:
        return self.clock()

    @property
    def tz(self) -> int:
        return self.cfg.tz_offset

    @property
    def settings(self) -> dict:
        return self.store.settings

    def send(self, text: str, markup: Optional[dict] = None) -> Optional[int]:
        return self.tg.send(text, markup)

    def wake(self, job: str) -> None:
        """Run a job on the next loop pass (e.g. after a setting changes)."""
        if job in self.jobs:
            self.jobs[job][1] = 0.0

    def log(self, msg: str) -> None:
        print(f"[hans] {msg}", file=sys.stderr, flush=True)

    # ---- data -----------------------------------------------------------
    def fresh_snapshot(self, max_age: float = 20) -> dict:
        """Latest snapshot, refetched if older than max_age seconds."""
        if self.snapshot is None or self.now() - self.snapshot_at > max_age:
            self.snapshot = bars.parse_snapshot(self.torn.user())
            self.snapshot_at = self.now()
        return self.snapshot

    def plans(self, codes: Optional[list] = None, needle: Optional[str] = None,
              now: Optional[float] = None) -> tuple[list, bool]:
        """Route plans for the given countries (default: enabled routes)."""
        routes = self.settings["depart"]["routes"]
        if codes is None:
            codes = [c for c in travel.COUNTRIES if (routes.get(c) or {}).get("enabled")]
        if not codes:
            return [], True
        stock = self.feeds.stock()
        if not stock:
            return [], False
        model = self.feeds.model()
        t = self.store["travel"]
        now = self.now() if now is None else now
        out = []
        for code in codes:
            item = needle if needle is not None else (routes.get(code) or {}).get("item", "")
            p = travel.plan(code, item, stock, model, t.setdefault("flights", {}),
                            t.setdefault("stock", {}), now, C.LAND_AFTER_RESTOCK)
            if p:
                out.append(p)
        return out, True

    def home_extras(self) -> list:
        """Extra Home lines: next 'leave now' and the OC time."""
        now, lines = self.now(), []
        try:
            plans, _ok = self.plans()
            deps = [p for p in plans if p.get("depart")]
            if deps and self.settings["depart"]["on"]:
                p = min(deps, key=lambda x: x["depart"])
                lines.append(f"🛫 Next leave: <b>{p['name']}</b> {clock(p['depart'], self.tz)} "
                             f"(in {dur(int(p['depart'] - now))})")
        except Exception as e:  # noqa: BLE001
            self.log(f"home extras (travel): {e}")
        ready = self.store["oc"].get("ready")
        if ready and ready > now:
            lines.append(f"⚔️ OC ready {clock(ready, self.tz)} (in {dur(int(ready - now))})")
        return lines

    def toggle_route(self, code: str) -> bool:
        routes = self.settings["depart"]["routes"]
        r = routes.setdefault(code, {"enabled": False,
                                     "item": S.DEFAULT_ROUTE_ITEMS.get(code, "")})
        r["enabled"] = not r.get("enabled")
        if r["enabled"]:
            self.settings["depart"]["on"] = True   # switching a route on implies alerts on
        self.wake("depart")
        return r["enabled"]
