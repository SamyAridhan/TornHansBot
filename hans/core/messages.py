"""Hans's voice: turn alert events into short, readable Telegram messages."""

from __future__ import annotations

import random
import time

from .fmt import fmt_duration_compact, html_escape, progress_bar

_EMOJI = {
    "energy_full": "⚡", "energy_mark": "⚡", "energy_increase": "⚡",
    "nerve_full": "🔴", "nerve_mark": "🔴", "nerve_increase": "🔴",
    "happy_full": "🙂",
    "energy_overflow": "⏳",
    "nerve_overflow": "⏳",
    "drug_cooldown_ended": "💊",
    "medical_cooldown_ended": "🩹",
    "booster_cooldown_ended": "🧪",
    "travel_landed": "✈️",
    "hospital_in": "🏥",
    "hospital_out": "🏥",
    "jail_in": "🚔",
    "jail_out": "🔓",
    "milestone": "🏆",
}

_FLAVOR = {
    "energy_full": [
        "Energy's maxed — hit the gym before it spills",
        "Full tank of Energy. Go move some iron, champ",
        "Energy topped out and nowhere to spend it. That's waste — train",
        "Energy's at the brim. The weights are calling",
        "Capped on Energy. Any more and it just evaporates",
    ],
    "nerve_full": [
        "Nerve's full — this city won't rob itself",
        "Full Nerve. Go make some poor life choices",
        "Nerve maxed out. Time to earn your reputation",
        "Nerve's brimming — somewhere, a crime is waiting",
        "All the Nerve you can hold. Put it to work",
    ],
    "happy_full": [
        "Happy's maxed — prime time to train hard",
        "Happy is full. Your gains will thank you",
        "Peak Happy. Hit the gym while it lasts",
    ],
    "energy_mark": [
        "Energy's over the line — enough to get moving",
        "Energy's stacked up. Time to spend some",
        "Enough Energy in the tank to do real work",
    ],
    "nerve_mark": [
        "Nerve's over the line — enough for a job or two",
        "Nerve banked. Go put it to work",
        "Enough Nerve for some mischief",
    ],
    "energy_increase": [
        "Energy ticking up",
        "Energy on the rise",
    ],
    "nerve_increase": [
        "Nerve creeping up",
        "Nerve on the rise",
    ],
    "energy_overflow": [
        "Energy's about to cap — spend it before it spills",
        "Energy nearly full. Burn some before regen goes to waste",
        "Energy's topping off soon — don't leave gains on the table",
    ],
    "nerve_overflow": [
        "Nerve's about to cap — go spend it on a crime",
        "Nerve nearly full. Dump it before the regen wastes",
        "Nerve's topping off soon — put it to work",
    ],
    "hospital_in": [
        "You've been hospitalized",
        "Ouch — you're in hospital",
        "Laid up in hospital",
    ],
    "hospital_out": [
        "You're out of hospital. Back on your feet",
        "Discharged — out of hospital and good to go",
        "Hospital's done with you. Get back to it",
    ],
    "jail_in": [
        "You've been thrown in jail",
        "Busted — you're in jail",
    ],
    "jail_out": [
        "You're out of jail. Lay low for a bit",
        "Sprung from jail — you're a free agent again",
        "Jail's behind you. Try not to make it a habit",
    ],
    "milestone": [
        "Milestone — you cracked {value} total stats! Now at {total}",
        "{value} total stats and climbing. Sitting at {total} now",
        "Big one: {value} total cleared. You're at {total}",
    ],
    "drug_cooldown_ended": [
        "Drug cooldown's up — you're clear to dose again",
        "Drug cooldown cleared. The pharmacy's open, so to speak",
        "Off drug cooldown. Re-dose when you're ready",
    ],
    "medical_cooldown_ended": [
        "Medical cooldown's up — patch kit's ready when you are",
        "Off medical cooldown. The doc will see you now",
    ],
    "booster_cooldown_ended": [
        "Booster cooldown cleared. Stock up",
        "Off booster cooldown — top yourself up",
    ],
    "travel_landed": [
        "Touched down in {dest}. Try to stay out of the papers",
        "Landed in {dest} — business awaits",
        "Arrived in {dest}. Watch your back",
    ],
}


def _split_flavor(text: str) -> list[str]:
    """Split at em-dashes / sentence periods so each clause gets its own line."""
    segs: list[str] = []
    for chunk in text.split("—"):
        for s in chunk.replace(". ", ".\n").split("\n"):
            s = s.strip()
            if s:
                segs.append(s)
    return segs or [text.strip()]


def format_event(ev: dict, now: float | None = None) -> str:
    t = ev["type"]
    pool = _FLAVOR.get(t)
    emoji = _EMOJI.get(t, "•")
    if not pool:
        return f"• {html_escape(t)}"
    flavor = random.choice(pool).format(
        dest=html_escape(ev.get("destination") or "your destination"),
        value=f"{ev.get('value', 0):,}", total=f"{ev.get('total', 0):,}")
    segs = _split_flavor(flavor)
    head = "\n".join((f"{emoji} <b>{s}</b>" if i == 0 else f"<b>{s}</b>")
                     for i, s in enumerate(segs))

    cur = ev.get("current")
    if ev.get("bar") and isinstance(cur, int):
        mx = ev.get("maximum") or 0
        stat = f"<code>{cur}/{mx}</code>" if mx else f"<code>{cur}</code>"
        lines = [head, f"{stat} {progress_bar(cur, mx)}"]
        eta = ev.get("eta_full")
        if t.endswith("_mark") and eta:
            lines.append(f"🔜 full in <b>{fmt_duration_compact(eta)}</b>")
        elif t.endswith("_overflow") and eta:
            lines.append(f"⏳ caps in <b>{fmt_duration_compact(eta)}</b>")
        return "\n".join(lines)

    if t in ("hospital_in", "jail_in") and ev.get("until"):
        remaining = int(ev["until"] - (now if now is not None else time.time()))
        if remaining > 0:
            return f"{head}\n🩹 out in <b>{fmt_duration_compact(remaining)}</b>"
    return head


def format_message(events: list[dict], now: float | None = None) -> str:
    return "\n\n".join(format_event(e, now) for e in events)


def preview_samples(now: float) -> list[dict]:
    """One of each common alert, for the /preview command."""
    return [
        {"type": "energy_mark", "bar": "energy", "current": 25, "maximum": 100,
         "mark": 25, "eta_full": 13440},
        {"type": "energy_overflow", "bar": "energy", "current": 95, "maximum": 100,
         "eta_full": 120},
        {"type": "nerve_full", "bar": "nerve", "current": 25, "maximum": 25},
        {"type": "drug_cooldown_ended", "cooldown": "drug"},
        {"type": "hospital_in", "until": int(now) + 720},
        {"type": "milestone", "value": 50000, "total": 61250},
    ]
