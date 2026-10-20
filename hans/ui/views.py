"""Screen text. Pure functions: data in, Telegram-HTML string out."""

from __future__ import annotations

from typing import Optional

from .. import settings as S
from ..core import bars, oc as ocm, travel
from ..core.fmt import clock, fmt_duration_compact as dur, html_escape, local_dt

HELP = (
    "🗂 <b>Hans — quick guide</b>\n"
    "Everything is on buttons: type /menu any time.\n"
    "\n"
    "🏠 <b>Home</b> — live bars, cooldowns, travel (also pinned at the top).\n"
    "✈️ <b>Travel</b> — stock, next restock and <b>when to leave</b> per country; "
    "turn on 'leave now' alerts. You get a buzz 1 min before you land abroad.\n"
    "⚔️ <b>OC</b> — reminders 24h / 12h / 8h before your crime is ready.\n"
    "💪 <b>Stats</b> — battle stats, digest &amp; chart on demand.\n"
    "⚙️ <b>Settings</b> — tap any alert to switch it on/off.\n"
    "\n"
    "<b>Shortcuts:</b> /status · /stock china · /price 206 · /oc in 12h\n"
    "\n"
    "<i>I only read your data — you make every move.</i>"
)


# --------------------------------------------------------------------------
# Home / status
# --------------------------------------------------------------------------
def _bar_row(emoji: str, label: str, b: dict, reg: dict) -> str:
    cur, mx = b.get("current", 0), b.get("maximum", 0)
    if mx and cur >= mx:
        tail = "full"
    else:
        secs = bars.seconds_to_full(cur, mx, reg)
        prefix = ""
        if secs is None and label == "Life":
            secs, prefix = bars.seconds_to_full_life(cur, mx), "~"
        tail = (prefix + dur(secs)) if secs is not None else "?"
    return f"{emoji} {label:<6}{f'{cur}/{mx}':>9}  {tail}"


def status(snapshot: dict, now: float, tz: int, extras: Optional[list] = None) -> str:
    reg = snapshot.get("regen", {})
    rows = [_bar_row(e, l, snapshot.get(k, {}), reg.get(k, {})) for e, l, k in (
        ("⚡", "Energy", "energy"), ("🔴", "Nerve", "nerve"),
        ("🙂", "Happy", "happy"), ("❤️", "Life", "life"))]
    out = [f"🗂 <b>Hans</b> · {clock(now, tz)}", "<pre>" + "\n".join(rows) + "</pre>"]

    st = snapshot.get("status", {})
    cat = bars.state_category(st.get("state", ""))
    left = int(st.get("until", 0) - now)
    if cat in ("hospital", "jail") and left > 0:
        out.append(("🏥 <b>In hospital</b>" if cat == "hospital" else "🚔 <b>In jail</b>")
                   + f" · out in {dur(left)}")

    cd = snapshot.get("cooldowns", {})
    busy = [f"{n} {dur(cd.get(k, 0))}" for k, n in
            (("drug", "Drug"), ("medical", "Med"), ("booster", "Boost")) if cd.get(k, 0) > 0]
    out.append("⏲ Cooldowns: " + (" · ".join(busy) if busy else "all clear ✅"))

    tv = snapshot.get("travel", {})
    if tv.get("time_left", 0) > 0:
        dest = html_escape(tv.get("destination") or "somewhere")
        out.append(f"✈️ To <b>{dest}</b> · lands {clock(now + tv['time_left'], tz)} "
                   f"(in {dur(tv['time_left'])})")
    out += extras or []
    return "\n".join(out)


def next_full(snapshot: dict, now: float, tz: int) -> str:
    reg = snapshot.get("regen", {})
    out = ["⏱ <b>Next full</b>"]
    for emoji, label, key in (("⚡", "Energy", "energy"), ("🔴", "Nerve", "nerve"),
                              ("🙂", "Happy", "happy"), ("❤️", "Life", "life")):
        b = snapshot.get(key, {})
        cur, mx = b.get("current", 0), b.get("maximum", 0)
        secs = bars.seconds_to_full(cur, mx, reg.get(key, {}))
        approx = ""
        if secs is None and key == "life":
            secs, approx = bars.seconds_to_full_life(cur, mx), "≈"
        if mx and cur >= mx:
            out.append(f"{emoji} <b>{label}</b> — already full ({cur}/{mx})")
        elif secs:
            out.append(f"{emoji} <b>{label}</b> {cur}/{mx} — {approx}{dur(secs)} "
                       f"(≈{clock(now + secs, tz)})")
        else:
            out.append(f"{emoji} <b>{label}</b> {cur}/{mx}")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Stats / price
# --------------------------------------------------------------------------
def stats(st: dict, prev_row: Optional[dict]) -> str:
    g = lambda k: f"{st.get(k, 0):,}"  # noqa: E731
    out = ["💪 <b>Battle stats</b>", "",
           f"<code>STR {g('strength'):>13}</code>",
           f"<code>DEF {g('defense'):>13}</code>",
           f"<code>SPD {g('speed'):>13}</code>",
           f"<code>DEX {g('dexterity'):>13}</code>",
           f"<b><code>TOT {g('total'):>13}</code></b>"]
    if prev_row:
        try:
            gained = st.get("total", 0) - int(prev_row.get("total", 0))
            when = prev_row.get("timestamp", "last log")[:16].replace("T", " ")
            out += ["", f"<i>{'+' if gained >= 0 else ''}{gained:,} total since {when} UTC</i>"]
        except (TypeError, ValueError):
            pass
    return "\n".join(out)


def price(item_id: str, name: Optional[str], low: Optional[int]) -> str:
    who = html_escape(name) if name else f"item {html_escape(item_id)}"
    if low is None:
        return (f"💰 Couldn't read a market price for <b>{who}</b> right now "
                f"(no listings, or a bad item id).")
    return f"💰 <b>{who}</b> cheapest listing: <code>${low:,}</code>"


# --------------------------------------------------------------------------
# Travel
# --------------------------------------------------------------------------
def _depart_bits(p: dict, now: float, tz: int) -> str:
    if p.get("depart"):
        return f"leave <b>{clock(p['depart'], tz)}</b> (in {dur(int(p['depart'] - now))})"
    if not p.get("flight"):
        return "<i>fly there once so I learn the flight time</i>"
    return "<i>no reachable restock yet</i>"


def travel_screen(plans: list, settings: dict, snapshot: Optional[dict],
                  now: float, tz: int, feed_ok: bool) -> str:
    out = ["✈️ <b>Travel</b>"]
    tv = (snapshot or {}).get("travel", {})
    if tv.get("time_left", 0) > 0:
        out.append(f"In the air → <b>{html_escape(tv.get('destination') or '?')}</b>, "
                   f"lands {clock(now + tv['time_left'], tz)}")
    land = settings["landing"]
    out.append(f"🛬 Landing buzz: <b>{'ON' if land['on'] else 'OFF'}</b>"
               + (f" · {land['lead']}s before touchdown" if land["on"] else ""))
    if not settings["depart"]["on"]:
        out.append("🛫 'Leave now' alerts: <b>OFF</b> (Travel settings)")
    out.append("")
    if not feed_ok:
        out.append("⚠️ Stock feed unreachable right now — try 🔄 in a minute.")
    elif not plans:
        out.append("No countries watched yet. Tap one below and switch its alert on.")
    else:
        out.append("<b>Watched:</b>")
        for p in plans:
            rs = f"restock {clock(p['restock'], tz)}" if p.get("restock") else "restock ?"
            out.append(f"🟢 <b>{p['name']}</b> · {html_escape(p['item'])} "
                       f"<code>{p['qty']:,}</code>\n    {rs} · {_depart_bits(p, now, tz)}")
    out += ["", "<i>Tap a country for details.</i>"]
    return "\n".join(out)


def route_detail(p: Optional[dict], code: str, enabled: bool, now: float, tz: int,
                 land_after: int = 120) -> str:
    name = travel.country_name(code)
    if not p:
        return (f"📦 <b>{name}</b>\nNo stock data right now (feed may be briefly down "
                f"or the item isn't sold there).")
    out = [f"📦 <b>{name} — {html_escape(p['item'] or 'item')}</b>",
           f"Stock: <b>{p['qty']:,}</b>   Buy: <code>${p['cost']:,}</code>"]
    if p.get("restock"):
        rs = p["restock"]
        out.append(f"Next restock: <b>{clock(rs, tz)}</b> "
                   f"(in {'now' if rs <= now else dur(int(rs - now))})"
                   + (f" · every {dur(p['cycle'])}" if p.get("cycle") else ""))
    else:
        out.append("<i>No restock timing available yet.</i>")
    if p.get("flight"):
        out.append(f"Flight: {dur(p['flight'])}")
    if p.get("depart"):
        out.append(f"🛫 <b>Leave at {clock(p['depart'], tz)}</b> "
                   f"(in {dur(max(0, int(p['depart'] - now)))}) → land ~{land_after // 60} min "
                   f"after a restock")
    elif p.get("restock"):
        out.append("🛫 " + _depart_bits(p, now, tz))
    out.append("")
    out.append(f"'Leave now' alert: <b>{'ON 🔔' if enabled else 'OFF'}</b>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# OC
# --------------------------------------------------------------------------
def oc_screen(oc_state: dict, settings: dict, now: float, tz: int) -> str:
    out = ["⚔️ <b>Organized Crime</b>"]
    ready = oc_state.get("ready")
    if ready:
        left = int(ready - now)
        when = f"{local_dt(ready, tz):%a %d/%m %H:%M}"
        out.append(f"Ready ≈ <b>{when}</b> ({'in ' + dur(left) if left > 0 else 'now'}) · "
                   f"{oc_state.get('source', 'auto')}")
        fired = oc_state.get("fired", [])
        marks = " ".join(f"{ocm.fmt_offset(o)} {'✅' if o in fired else '⏳'}"
                         for o in ocm.DEFAULT_OFFSETS)
        out.append(f"Reminders: {marks}")
    else:
        out.append("No OC time known yet. I try to read it from your faction; "
                   "if that doesn't work, tap when it's ready below.")
    if not settings["oc"]["on"]:
        out.append("🔕 Reminders are <b>OFF</b> (Settings › Daily &amp; weekly).")
    out += ["", "<i>Set it manually: tap a button, or type /oc 21/10 14:00</i>"]
    return "\n".join(out)


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------
_PAGE_NOTES = {
    "alerts": "Tap to switch an alert on/off. 'Energy reaches' cycles the level.",
    "travel": "Landing buzz pings every 2s from shortly before you land abroad until you tap ✅.",
    "daily": "Digest arrives at your chosen time; chart on Sundays 09:00.",
    "quiet": "During quiet hours, routine energy/nerve pings are held back. "
             "Important ones (hospital, landing, OC) still come through.",
}


def settings_home() -> str:
    return "⚙️ <b>Settings</b>\nPick a section — every option is one tap."


def settings_page(page: str) -> str:
    return f"{S.PAGES[page][0]}\n<i>{_PAGE_NOTES[page]}</i>"


def land_buzz(dest: str, secs_left: float) -> str:
    """Buzz text: a countdown before touchdown, then 'landed — buy now'."""
    d = html_escape(dest or "your destination")
    s = int(secs_left)
    if s > 0:
        return (f"🛬 <b>{d} — landing in {s}s!</b>\n"
                f"Get your buy ready, hit it the moment you land, then fly out.")
    return (f"🛬 <b>LANDED in {d} — buy now!</b>\n"
            f"Buy and fly out fast (idling = mug risk).")
