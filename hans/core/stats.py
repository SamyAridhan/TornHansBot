"""Battle-stat history (history.csv), the morning digest, and market parsing."""

from __future__ import annotations

import csv
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .bars import seconds_to_full
from .fmt import fmt_duration_compact

HISTORY_HEADER = ["timestamp", "strength", "defense", "speed", "dexterity", "total", "nnb"]


# --------------------------------------------------------------------------
# history.csv
# --------------------------------------------------------------------------
def append_history(path: str, timestamp: str, stats: dict, nnb: int) -> None:
    new_file = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(HISTORY_HEADER)
        w.writerow([timestamp] + [stats.get(k, 0) for k in
                                  ("strength", "defense", "speed", "dexterity", "total")]
                   + [nnb])


def read_history(path: str) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def _ts(s: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat((s or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def load_series(rows: list[dict]) -> list[dict]:
    """Rows -> sorted [{dt, total, strength, defense, speed, dexterity}]."""
    out = []
    for r in rows:
        dt = _ts(r.get("timestamp", ""))
        if not dt:
            continue
        try:
            out.append({"dt": dt, **{k: int(r.get(k, 0)) for k in
                        ("total", "strength", "defense", "speed", "dexterity")}})
        except (TypeError, ValueError):
            continue
    return sorted(out, key=lambda x: x["dt"])


def weekly_gain(series: list[dict]) -> Optional[int]:
    if len(series) < 2:
        return None
    cutoff = series[-1]["dt"].timestamp() - 7 * 86400
    base = series[0]
    for row in series:
        if row["dt"].timestamp() <= cutoff:
            base = row
        else:
            break
    return series[-1]["total"] - base["total"]


# --------------------------------------------------------------------------
# Digest
# --------------------------------------------------------------------------
HANS_LINES = [
    "Slow is smooth, smooth is fast. Keep stacking.",
    "The city rewards the patient and punishes the greedy. Be patient.",
    "Every rep counts. So does every nerve point you don't waste.",
    "Quiet grind today. Nobody climbs the ladder in one jump.",
    "Train like someone stronger is coming — because they are.",
    "Discipline beats motivation. Show up anyway.",
    "Small gains compound. That's the whole game.",
    "Don't chase fights you can't win. Chase stats you can.",
    "A good day is a boring day: gym, crimes, repeat.",
    "Keep your head down and your stats up.",
    "The best time to train was yesterday. The second best is now.",
    "Spend the energy, bank the nerve, trust the process.",
    "Consistency is the only cheat code that isn't bannable.",
    "You against yesterday-you. Win that one.",
]


def line_of_the_day(d: datetime) -> str:
    return HANS_LINES[d.toordinal() % len(HANS_LINES)]


def daily_last_totals(rows: list[dict], tz_offset: int = 8) -> list[tuple[str, int]]:
    by_day: dict[str, int] = {}
    for r in rows:
        dt = _ts(r.get("timestamp", ""))
        try:
            total = int(r.get("total", 0))
        except (TypeError, ValueError):
            continue
        if dt:
            by_day[(dt + timedelta(hours=tz_offset)).date().isoformat()] = total
    return sorted(by_day.items())


def current_streak(daily: list[tuple[str, int]]) -> int:
    streak = 0
    for i in range(len(daily) - 1, 0, -1):
        d_cur = datetime.fromisoformat(daily[i][0]).date()
        d_prev = datetime.fromisoformat(daily[i - 1][0]).date()
        if daily[i][1] > daily[i - 1][1] and (d_cur - d_prev).days == 1:
            streak += 1
        else:
            break
    return streak


def gain_last_n_days(daily: list[tuple[str, int]], n: int = 7) -> Optional[int]:
    if len(daily) < 2:
        return None
    cutoff = datetime.fromisoformat(daily[-1][0]).date() - timedelta(days=n)
    base = None
    for day, total in daily:
        if datetime.fromisoformat(day).date() <= cutoff:
            base = total
        else:
            break
    return daily[-1][1] - (base if base is not None else daily[0][1])


def format_digest(snapshot: dict, stats: dict, daily: list[tuple[str, int]],
                  now_utc: datetime, tz_offset: int = 8) -> str:
    local = now_utc + timedelta(hours=tz_offset)
    lines = [f"🌅 <b>Morning, boss.</b> {local:%a %d %b}", ""]
    total = stats.get("total", 0)
    if total:
        line = f"💪 Total stats <b>{total:,}</b>"
        if len(daily) >= 2:
            g = daily[-1][1] - daily[-2][1]
            line += f"  <i>({'+' if g >= 0 else ''}{g:,} day-on-day)</i>"
        lines.append(line)
    streak = current_streak(daily)
    if streak:
        lines.append(f"🔥 Streak: <b>{streak} day{'s' if streak != 1 else ''}</b> of gains")
    wk = gain_last_n_days(daily, 7)
    if wk is not None:
        lines.append(f"📈 Last 7 days: <b>+{wk:,}</b> total")
    reg = snapshot.get("regen", {})
    rows = []
    for emoji, label, key in (("⚡", "Energy", "energy"), ("🔴", "Nerve ", "nerve")):
        b = snapshot.get(key, {})
        cur, mx = b.get("current", 0), b.get("maximum", 0)
        tail = "full" if mx and cur >= mx else fmt_duration_compact(
            seconds_to_full(cur, mx, reg.get(key, {})))
        rows.append(f"{emoji} {label} {cur}/{mx}  {tail}")
    lines += ["", "<pre>" + "\n".join(rows) + "</pre>",
              "", f"<i>“{line_of_the_day(local)}”</i>"]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Item market (v2)
# --------------------------------------------------------------------------
def parse_market(raw: dict) -> Optional[int]:
    """Lowest listing price, defensively across payload shapes."""
    im = raw.get("itemmarket", raw.get("bazaar"))
    if im is None:
        im = raw
    listings = im.get("listings") if isinstance(im, dict) else im
    if not isinstance(listings, list):
        return None
    prices = []
    for entry in listings:
        if not isinstance(entry, dict):
            continue
        for key in ("price", "cost", "value"):
            if entry.get(key) is not None:
                try:
                    prices.append(int(entry[key]))
                    break
                except (TypeError, ValueError):
                    pass
    return min(prices) if prices else None


def parse_market_item(raw: dict) -> dict:
    im = raw.get("itemmarket", raw.get("bazaar"))
    item = im.get("item") if isinstance(im, dict) else None
    item = item if isinstance(item, dict) else {}
    name, image = item.get("name"), item.get("image")
    return {"name": name if isinstance(name, str) else None,
            "image": image if isinstance(image, str) and image.startswith("http") else None}
