"""The pinned live dashboard: one message at the top of the chat, showing Home,
kept fresh by editing in place. If you navigate inside it, it waits until
you've been idle a while before flipping back to Home."""

from __future__ import annotations

from .. import config as C
from ..ui import keyboards as K, views as V


def home_text(ctx) -> str:
    return "📌 " + V.status(ctx.fresh_snapshot(60), ctx.now(), ctx.tz, ctx.home_extras())


def run(ctx) -> float:
    if not ctx.settings["pin"]["on"]:
        return 600
    tg = ctx.store["tg"]
    now = ctx.now()
    if tg.get("pin_view", "home") != "home" and now - tg.get("pin_view_at", 0) < C.PIN_IDLE_RESET:
        return 60
    text = home_text(ctx)
    pid = tg.get("pin_id")
    if pid and text == tg.get("pin_text") and tg.get("pin_view", "home") == "home":
        return C.PIN_REFRESH
    ok = bool(pid) and ctx.tg.edit(pid, text, K.HOME).get("ok")
    if not ok:
        pid = ctx.tg.send(text, K.HOME)
        if pid:
            ctx.tg.pin(pid)
    if pid:
        tg.update({"pin_id": pid, "pin_text": text, "pin_view": "home"})
    return C.PIN_REFRESH
