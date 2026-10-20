"""Community travel feeds (free, no key): live foreign stock from Prombot
(YATA fallback) and the restock-timing model. Cached so the depart watcher,
Travel screen and Home don't refetch on every tap — and a feed outage is
remembered for a minute so screens never hang waiting on it."""

from __future__ import annotations

import time
from typing import Callable

from .. import config
from ..core import travel
from .http import get_json

PROMBOT_URL = "https://api.prombot.co.uk/api/travel"
YATA_URL = "https://yata.yt/api/v1/travel/export/"
RESTOCK_MODEL_URL = ("https://raw.githubusercontent.com/russianrob/"
                     "torn-foreign-restock/main/restock-model.json")
FAIL_BACKOFF = 60
TIMEOUT = 10


def _get(url: str) -> dict:
    return get_json(url, timeout=TIMEOUT)


class Feeds:
    def __init__(self, getter: Callable[[str], dict] = _get,
                 clock: Callable[[], float] = time.time):
        self.get = getter
        self.clock = clock
        self._stock = (0.0, {})
        self._model = (0.0, {})
        self._stock_fail = -1e9
        self._model_fail = -1e9

    def stock(self) -> dict:
        """Parsed stock {code: {update, items:[...]}}, or {} if both feeds fail."""
        now = self.clock()
        at, val = self._stock
        if val and now - at < config.STOCK_CACHE:
            return val
        if now - self._stock_fail < FAIL_BACKOFF:
            return val
        for url in (PROMBOT_URL, YATA_URL):
            try:
                parsed = travel.parse_stock(self.get(url))
            except Exception:  # noqa: BLE001 — try the fallback
                continue
            if parsed:
                self._stock = (now, parsed)
                return parsed
        self._stock_fail = now
        return val

    def model(self) -> dict:
        now = self.clock()
        at, val = self._model
        if val and now - at < config.MODEL_CACHE:
            return val
        if now - self._model_fail < FAIL_BACKOFF:
            return val
        try:
            m = self.get(RESTOCK_MODEL_URL)
            if isinstance(m, dict) and m:
                self._model = (now, m)
                return m
        except Exception:  # noqa: BLE001
            pass
        self._model_fail = now
        return val
