"""Bars job: one Torn fetch feeds everything — bar/cooldown/hospital alerts,
the dashboard snapshot, flight-time learning, and arming the landing buzz."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import config as C
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
    """Learn flight times and (re)arm / clear the pending landing."""
    tv = snap.get("travel", {})
    tl, dest = tv.get("time_left", 0), tv.get("destination") or ""
    land = ctx.store["landing"]
    if tl > 0 and travel.is_foreign(dest):
        travel.learn_flight(ctx.store["travel"].setdefault("flights", {}),
                            travel.code_for(dest) or "", tl)
        arrival = now + tl
        if (not land.get("arrival") or land.get("dest") != dest
                or (abs(float(land["arrival"]) - arrival) > 8 and not land.get("alerted"))):
            land.clear()
            land.update({"arrival": arrival, "dest": dest, "alerted": False})
            ctx.wake("landing")
    elif tl > 0:
        land.clear()                      # heading home: no buzz for Torn
    elif land.get("arrival") and (land.get("alerted") or now > float(land["arrival"]) + 20):
        land.clear()                      # landed (already buzzed) or stale
