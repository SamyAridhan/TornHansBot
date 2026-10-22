"""Everything you can change from Telegram's Settings screens.

Settings live inside the state file (section "settings") and are merged over
these defaults on every load, so adding a new option never breaks old state.

Each tappable option is described once in SPEC: the button key used in
callback data, where it lives, its label, and either "toggle" (on/off) or a
list of choices to cycle through. The UI renders straight from SPEC.
"""

from __future__ import annotations

import copy
from typing import Any

# Default "what to buy" per country — the usual best plushie/flower run.
DEFAULT_ROUTE_ITEMS = {
    "mex": "Jaguar Plushie",
    "cay": "Stingray Plushie",
    "can": "Wolverine Plushie",
    "haw": "Orchid",
    "uni": "Red Fox Plushie",
    "arg": "Monkey Plushie",
    "swi": "Chamois Plushie",
    "jap": "Cherry Blossom",
    "chi": "Panda Plushie",
    "uae": "Camel Plushie",
    "sou": "Lion Plushie",
}

DEFAULTS: dict = {
    "alerts": {
        "energy_full": True,
        "energy_mark": 25,          # 0 = off
        "energy_overflow": True,
        "nerve_full": True,
        "nerve_overflow": True,
        "happy_full": False,
        "drug_cd": True,
        "medical_cd": False,
        "booster_cd": False,
        "hospital": True,
        "jail": True,
        "milestone": True,
    },
    "landing": {"on": True, "lead": 60, "home": True},
    "depart": {
        "on": True,
        "routes": {code: {"enabled": code == "chi", "item": item}
                   for code, item in DEFAULT_ROUTE_ITEMS.items()},
    },
    "oc": {"on": True},
    "digest": {"on": True, "time": "08:30"},
    "chart": {"on": True},           # Sundays 09:00
    "quiet": {"on": True, "window": "01-08"},
    "pin": {"on": True},
}

# key -> (path, label, kind) ; kind is "toggle" or a list of choices
SPEC: dict = {
    # Alerts
    "ef": ("alerts.energy_full", "Energy full", "toggle"),
    "em": ("alerts.energy_mark", "Energy reaches", [0, 25, 50, 100, 150]),
    "eo": ("alerts.energy_overflow", "Energy cap soon", "toggle"),
    "nf": ("alerts.nerve_full", "Nerve full", "toggle"),
    "no": ("alerts.nerve_overflow", "Nerve cap soon", "toggle"),
    "hf": ("alerts.happy_full", "Happy full", "toggle"),
    "dc": ("alerts.drug_cd", "Drug CD done", "toggle"),
    "mc": ("alerts.medical_cd", "Medical CD done", "toggle"),
    "bc": ("alerts.booster_cd", "Booster CD done", "toggle"),
    "ho": ("alerts.hospital", "Hospital in/out", "toggle"),
    "ja": ("alerts.jail", "Jail in/out", "toggle"),
    "ms": ("alerts.milestone", "Milestones", "toggle"),
    # Travel
    "lb": ("landing.on", "Landing buzz", "toggle"),
    "ll": ("landing.lead", "Buzz before landing", [30, 60, 90, 120]),
    "da": ("depart.on", "'Leave now' alerts", "toggle"),
    "lh": ("landing.home", "'Back in Torn' ping", "toggle"),
    # Daily & weekly
    "dg": ("digest.on", "Morning digest", "toggle"),
    "dt": ("digest.time", "Digest time",
           ["07:00", "07:30", "08:00", "08:30", "09:00", "10:00"]),
    "wc": ("chart.on", "Sunday growth chart", "toggle"),
    "oc": ("oc.on", "OC reminders (24/12/8h)", "toggle"),
    "pn": ("pin.on", "Pinned live dashboard", "toggle"),
    # Quiet hours
    "qh": ("quiet.on", "Quiet hours", "toggle"),
    "qw": ("quiet.window", "Quiet window", ["00-07", "01-08", "23-07", "02-09"]),
}

PAGES: dict = {
    "alerts": ("🔔 Alerts", ["ef", "em", "eo", "nf", "no", "hf",
                            "dc", "mc", "bc", "ho", "ja", "ms"]),
    "travel": ("✈️ Travel", ["lb", "ll", "lh", "da"]),
    "daily": ("📅 Daily & weekly", ["dg", "dt", "wc", "oc", "pn"]),
    "quiet": ("🌙 Quiet hours", ["qh", "qw"]),
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def with_defaults(saved: dict) -> dict:
    """Saved settings layered over DEFAULTS (new options appear automatically)."""
    return _merge(DEFAULTS, saved or {})


def get(settings: dict, path: str) -> Any:
    cur: Any = settings
    for part in path.split("."):
        cur = (cur or {}).get(part) if isinstance(cur, dict) else None
    return cur


def put(settings: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cur = settings
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def apply(settings: dict, key: str) -> bool:
    """Tap a settings button: flip a toggle or advance a cycle. False if unknown."""
    spec = SPEC.get(key)
    if not spec:
        return False
    path, _label, kind = spec
    cur = get(settings, path)
    if kind == "toggle":
        put(settings, path, not bool(cur))
    else:
        i = kind.index(cur) if cur in kind else -1
        put(settings, path, kind[(i + 1) % len(kind)])
    return True


def show_value(key: str, value: Any) -> str:
    """How a setting's value reads on its button."""
    kind = SPEC[key][2]
    if kind == "toggle":
        return "✅" if value else "⬜"
    if key == "em":
        return "off" if not value else str(value)
    if key == "ll":
        return f"{value}s"
    if key == "qw":
        a, b = str(value).split("-")
        return f"{a}:00–{b}:00"
    return str(value)


def quiet_bounds(settings: dict) -> tuple[int, int]:
    try:
        a, b = str(get(settings, "quiet.window")).split("-")
        return int(a), int(b)
    except (ValueError, AttributeError):
        return 1, 8


def digest_hm(settings: dict) -> tuple[int, int]:
    try:
        h, m = str(get(settings, "digest.time")).split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return 8, 30


def alert_config(settings: dict) -> dict:
    """Translate settings into the rule config core.bars.evaluate() reads."""
    a = settings["alerts"]
    qs, qe = quiet_bounds(settings)
    dh, dm = digest_hm(settings)
    return {
        "alerts": {
            "energy_full": a["energy_full"],
            "nerve_full": a["nerve_full"],
            "happy_full": a["happy_full"],
            "drug_cooldown_ended": a["drug_cd"],
            "medical_cooldown_ended": a["medical_cd"],
            "booster_cooldown_ended": a["booster_cd"],
            "energy_overflow": a["energy_overflow"],
            "nerve_overflow": a["nerve_overflow"],
            "hospital_in": a["hospital"], "hospital_out": a["hospital"],
            "jail_in": a["jail"], "jail_out": a["jail"],
            "milestone": a["milestone"],
        },
        "marks": {"energy": a["energy_mark"] or None, "nerve": None},
        "overflow_lead_seconds": 600,
        "milestones": [
            5_000, 10_000, 25_000, 50_000, 100_000, 250_000, 500_000,
            1_000_000, 2_000_000, 5_000_000, 10_000_000, 25_000_000,
            50_000_000, 100_000_000,
        ],
        "quiet_hours": {"enabled": settings["quiet"]["on"], "start": qs, "end": qe},
        "schedule": {
            "digest": {"enabled": settings["digest"]["on"], "hour": dh, "minute": dm},
            "chart": {"enabled": settings["chart"]["on"],
                      "weekday": 6, "hour": 9, "minute": 0},
        },
    }
