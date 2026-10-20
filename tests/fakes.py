"""Test doubles: a fake clock, Telegram, Torn and feeds, and a ctx builder."""

from __future__ import annotations

import os
import tempfile

from hans.config import Config
from hans.context import Ctx
from hans.store import Store


class Clock:
    def __init__(self, t: float = 1_800_000_000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class FakeTG:
    def __init__(self, chat_id="42"):
        self.chat_id = chat_id
        self.sent = []          # (mid, text, markup)
        self.edits = []         # (mid, text, markup)
        self.calls = []         # (method, payload)
        self.answers = []
        self.pinned = []
        self.updates = []       # queued update batches
        self.timeouts = []
        self.photos = []
        self._mid = 100
        self.edit_ok = True

    def send(self, text, markup=None, silent=False):
        self._mid += 1
        self.sent.append((self._mid, text, markup))
        return self._mid

    def edit(self, mid, text, markup=None):
        self.edits.append((mid, text, markup))
        return {"ok": self.edit_ok}

    def call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True}

    def pin(self, mid):
        self.pinned.append(mid)

    def answer(self, cq_id, text=""):
        self.answers.append((cq_id, text))

    def photo_url(self, url, caption, markup=None):
        self.photos.append((url, caption))
        return True

    def photo_bytes(self, png, caption):
        self.photos.append((len(png), caption))
        return True

    def set_commands(self, cmds):
        self.calls.append(("setMyCommands", cmds))

    def get_updates(self, offset, timeout):
        self.timeouts.append(timeout)
        return self.updates.pop(0) if self.updates else []

    # helpers
    def texts(self):
        return [t for _m, t, _k in self.sent]


def bar(cur, mx, inc=5, interval=600, tick=0, full=0):
    return {"current": cur, "maximum": mx, "increment": inc, "interval": interval,
            "ticktime": tick, "fulltime": full}


def user_payload(**over):
    p = {"energy": bar(10, 150, full=8000), "nerve": bar(5, 25, inc=1, interval=300, full=6000),
         "happy": bar(100, 5000), "life": {"current": 400, "maximum": 400},
         "cooldowns": {"drug": 0, "medical": 0, "booster": 0},
         "travel": {"time_left": 0, "destination": "Torn"},
         "status": {"state": "Okay", "until": 0}, "player_id": 4538696, "total": 20000,
         "strength": 5000, "defense": 5000, "speed": 5000, "dexterity": 5000}
    p.update(over)
    return p


class FakeTorn:
    def __init__(self, payload=None, crimes=None, market=None):
        self.payload = payload or user_payload()
        self.crimes = crimes
        self.market_payload = market or {}
        self.user_calls = 0

    def user(self, selections="x"):
        self.user_calls += 1
        return self.payload

    def faction_crimes(self):
        return self.crimes

    def market(self, item_id):
        return self.market_payload


STOCK = {"chi": {"update": 0, "items": [
    {"id": 274, "name": "Panda Plushie", "qty": 800, "cost": 400, "restock": None}]},
    "mex": {"update": 0, "items": [
    {"id": 258, "name": "Jaguar Plushie", "qty": 50, "cost": 10000, "restock": None}]}}


class FakeFeeds:
    def __init__(self, stock=None, model=None):
        self._stock = STOCK if stock is None else stock
        self._model = model or {}

    def stock(self):
        return self._stock

    def model(self):
        return self._model


def make_ctx(torn=None, feeds=None, clock=None, legacy=None, state=None):
    d = tempfile.mkdtemp()
    for name, data in (legacy or {}).items():
        import json
        with open(os.path.join(d, name), "w") as f:
            json.dump(data, f)
    path = os.path.join(d, "hans_state.json")
    if state is not None:
        import json
        with open(path, "w") as f:
            json.dump(state, f)
    store = Store(path).load()
    cfg = Config(api_key="k", bot_token="t", chat_id="42", tz_offset=8,
                 state_path=path, history_path=os.path.join(d, "history.csv"))
    ctx = Ctx(cfg, store, FakeTG(), torn or FakeTorn(), feeds or FakeFeeds(),
              clock or Clock())
    from hans import jobs
    ctx.jobs = {name: [fn, 0.0] for name, fn in jobs.ALL}
    return ctx
