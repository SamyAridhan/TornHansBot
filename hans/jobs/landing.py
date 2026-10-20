"""Landing buzz: starts `lead` seconds before you touch down abroad and pings
every couple of seconds until you tap ✅ (or ~1 min after landing). When it
stops, the buzz messages are deleted so the chat stays clean."""

from __future__ import annotations

from .. import config as C
from ..ui import keyboards as K, views as V


def start(ctx, dest: str, arrival: float, stop: float, demo: bool = False) -> None:
    ctx.buzz = {"dest": dest, "arrival": arrival, "stop": stop, "demo": demo, "mids": []}
    ctx.wake("landing")


def stop(ctx, reason: str) -> None:
    """End the buzz: delete its messages, leave one short note."""
    b = ctx.buzz
    ctx.buzz = None
    if not b:
        return
    mids = [m for m in b.get("mids", []) if m]
    for i in range(0, len(mids), 100):
        ctx.tg.call("deleteMessages", {"chat_id": ctx.tg.chat_id,
                                       "message_ids": mids[i:i + 100]})
    if not b.get("demo"):
        ctx.store["landing"].clear()
    note = {"ack": "✅ Buzz off. Buy and fly out!",
            "timeout": "🔕 Buzz stopped. You've landed — buy and fly out!",
            }.get(reason, "🔕 Buzz stopped.")
    if b.get("demo"):
        note = "✅ Test buzz finished — that's what a landing feels like."
    ctx.send(note)


def run(ctx) -> float:
    now = ctx.now()
    b = ctx.buzz
    if b:
        if now >= b["stop"]:
            stop(ctx, "timeout")
            return 30
        mid = ctx.send(V.land_buzz(b["dest"], b["arrival"] - now), K.ACK)
        b["mids"].append(mid)
        return C.BUZZ_INTERVAL

    land = ctx.store["landing"]
    s = ctx.settings["landing"]
    arrival = land.get("arrival")
    if not (arrival and s.get("on") and not land.get("alerted")):
        return 300
    arrival = float(arrival)
    fire_at = arrival - int(s.get("lead", 60))
    if now < fire_at:
        return min(fire_at - now, 300)
    if now > arrival + 30:            # we missed it (bot was restarting) — drop
        land.clear()
        return 300
    land["alerted"] = True
    start(ctx, land.get("dest", ""), arrival, arrival + C.BUZZ_AFTER_LANDING)
    return 0
