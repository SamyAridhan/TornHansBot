"""OC reminders at 24h / 12h / 8h before your crime is ready."""

from __future__ import annotations

import json

from .. import config as C
from ..core import oc as ocm
from ..core.fmt import local_dt


def auto_read(ctx) -> bool:
    """Try to read the ready time from the faction API. True if found."""
    raw = ctx.torn.faction_crimes()
    if raw is None:
        return False
    if not ctx.flags.get("oc_logged"):
        ctx.log("OC crimes sample: " + json.dumps(raw)[:1200])
        ctx.flags["oc_logged"] = True
    uid = (ctx.snapshot or {}).get("player_id") or 0
    ready = ocm.parse_my_oc_ready(raw, uid)
    if not ready:
        return False
    st = ctx.store["oc"]
    old = st.get("ready")
    if ready != old:
        fired = st.get("fired", []) if old and abs(ready - old) < 3600 else []
        st.clear()
        st.update({"ready": ready, "source": "auto", "fired": fired})
    return True


def set_manual(ctx, ready: int) -> None:
    st = ctx.store["oc"]
    st.clear()
    st.update({"ready": int(ready), "source": "manual", "fired": []})
    ctx.wake("oc")


def run(ctx) -> float:
    st = ctx.store["oc"]
    if st.get("source") != "manual":
        auto_read(ctx)
    ready = st.get("ready")
    now = ctx.now()
    if not ready:
        return C.OC_POLL
    if ready < now - 3600:              # it's done — forget it, wait for the next
        st.clear()
        return C.OC_POLL
    if ctx.settings["oc"]["on"]:
        fired = st.setdefault("fired", [])
        for off in ocm.due_reminders(ready, now, fired=fired):
            ctx.send(f"⚔️ <b>OC in ~{ocm.fmt_offset(off)}</b> — ready ≈ "
                     f"{local_dt(ready, ctx.tz):%a %H:%M} your time. Be online to join!",
                     {"inline_keyboard": [[{"text": "⚔️ OC details",
                                            "callback_data": "new:oc"}]]})
            fired.append(off)
    return C.OC_POLL
