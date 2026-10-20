"""The single loop: run due jobs, then long-poll Telegram until the next job is
due (so a landing buzz fires on the exact second), answer taps, save state."""

from __future__ import annotations

import math
import time

from . import config as C
from . import jobs
from .context import Ctx
from .net.torn import TornApiError
from .ui import keyboards as K
from .ui.router import COMMANDS, handle_update

UPGRADE_NOTE = (
    "✨ <b>Hans got an upgrade.</b>\n"
    "Everything is now one bot with buttons — no commands to remember.\n"
    "• <b>Home</b> (pinned) shows your bars live\n"
    "• <b>Travel</b>: stock, restock, when to leave + 'leave now' alerts\n"
    "• <b>OC</b>: reminders 24h/12h/8h before\n"
    "• <b>Settings</b>: tap any alert on/off\n"
    "Your flight times, OC time and watchlist carried over. Type /menu any time."
)


def run_job(ctx: Ctx, name: str, fn) -> float:
    try:
        return max(0.0, float(fn(ctx)))
    except TornApiError as e:
        ctx.log(f"{name}: {e}")
        return 120.0
    except Exception as e:  # noqa: BLE001 — one bad pass never kills the bot
        ctx.log(f"{name} error: {type(e).__name__}: {e}")
        return 60.0


def startup(ctx: Ctx) -> None:
    ctx.tg.set_commands(COMMANDS)
    tg = ctx.store["tg"]
    if not tg.get("welcomed"):
        ctx.send(UPGRADE_NOTE if ctx.store.migrated or tg.get("offset") else
                 "👋 <b>Hans is online.</b> Everything's on the buttons below.", K.HOME)
        tg["welcomed"] = True
        ctx.store.save()


def tick(ctx: Ctx) -> float:
    """Run every due job once. Returns seconds until the next one is due."""
    for name, entry in ctx.jobs.items():
        if ctx.now() >= entry[1]:
            entry[1] = ctx.now() + run_job(ctx, name, entry[0])
    return min(e[1] for e in ctx.jobs.values()) - ctx.now()


def run(ctx: Ctx, seconds: int, sleep=time.sleep) -> None:
    ctx.jobs = {name: [fn, 0.0] for name, fn in jobs.ALL}
    startup(ctx)
    deadline = ctx.now() + seconds
    tg = ctx.store["tg"]
    ctx.log(f"running for ~{seconds}s (offset={tg.get('offset')})")
    while ctx.now() < deadline:
        wait = tick(ctx)
        ctx.store.save()
        timeout = int(min(C.LONG_POLL_MAX, max(0, math.ceil(wait))))
        timeout = min(timeout, max(0, int(deadline - ctx.now())))
        try:
            updates = ctx.tg.get_updates(tg.get("offset"), timeout)
        except Exception as e:  # noqa: BLE001 — network blip / 409 during handover
            ctx.log(f"getUpdates: {e}")
            sleep(3)
            continue
        for u in updates:
            tg["offset"] = u["update_id"] + 1
            try:
                handle_update(ctx, u)
            except Exception as e:  # noqa: BLE001
                ctx.log(f"update error: {type(e).__name__}: {e}")
        if updates:
            ctx.store.save()
    if ctx.buzz:
        from .jobs import landing
        landing.stop(ctx, "timeout")
    ctx.store.save()
    ctx.log("deadline reached; exiting for the next run to take over")
