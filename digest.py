#!/usr/bin/env python3
"""
Torn Watch — daily digest.

One friendly morning message: your total-stat gain since yesterday, a gains
streak, a 7-day trend, your current bars, and a Hans "line of the day". Reads
the same history.csv the dashboard uses, so it costs almost nothing.

Read-only, like everything else. Run from the digest.yml workflow (cron) or
manually with `python3 digest.py` / `python3 digest.py --dry-run`.
"""

from __future__ import annotations

import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Optional

import torn_watch as tw
import tier2

DEFAULT_TZ_OFFSET = 8

# Deterministic "line of the day" — same line all day, rotates by date.
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


# --------------------------------------------------------------------------
# Pure history analysis
# --------------------------------------------------------------------------
def daily_last_totals(rows: list[dict], tz_offset: int = DEFAULT_TZ_OFFSET) -> list[tuple[str, int]]:
    """Collapse history rows to one (local-date, last-total) per day, sorted."""
    by_day: dict[str, int] = {}
    for r in rows:
        ts = r.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            total = int(r.get("total", 0))
        except (ValueError, TypeError):
            continue
        day = (dt + timedelta(hours=tz_offset)).date().isoformat()
        by_day[day] = total   # later rows overwrite -> last reading of the day
    return sorted(by_day.items())


def current_streak(daily: list[tuple[str, int]]) -> int:
    """Consecutive calendar days (ending at the latest) that each gained.

    Breaks on a non-gaining day or a missing calendar day."""
    if len(daily) < 2:
        return 0
    streak = 0
    for i in range(len(daily) - 1, 0, -1):
        d_cur = datetime.fromisoformat(daily[i][0]).date()
        d_prev = datetime.fromisoformat(daily[i - 1][0]).date()
        gained = daily[i][1] > daily[i - 1][1]
        consecutive = (d_cur - d_prev).days == 1
        if gained and consecutive:
            streak += 1
        else:
            break
    return streak


def gain_last_n_days(daily: list[tuple[str, int]], n: int = 7) -> Optional[int]:
    """Total gained over roughly the last n days (latest minus the reading
    ~n days earlier). None if we can't span that far."""
    if len(daily) < 2:
        return None
    latest_total = daily[-1][1]
    latest_day = datetime.fromisoformat(daily[-1][0]).date()
    cutoff = latest_day - timedelta(days=n)
    base = None
    for day, total in daily:
        if datetime.fromisoformat(day).date() <= cutoff:
            base = total
        else:
            break
    if base is None:
        base = daily[0][1]   # not enough history; span from the earliest
    return latest_total - base


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------
def format_digest(snapshot: dict, stats: dict, daily: list[tuple[str, int]],
                  now_utc: datetime, tz_offset: int = DEFAULT_TZ_OFFSET) -> str:
    local = now_utc + timedelta(hours=tz_offset)
    total = stats.get("total", 0)
    lines = [f"🌅 <b>Morning, boss.</b> {local:%a %d %b}", ""]

    if total:
        line = f"💪 Total stats <b>{total:,}</b>"
        if daily:
            yday_gain = total - daily[-1][1] if len(daily) >= 1 else 0
            # if today's a new day vs last logged, compare to last logged total
            if len(daily) >= 2:
                yday_gain = daily[-1][1] - daily[-2][1]
            sign = "+" if yday_gain >= 0 else ""
            line += f"  <i>({sign}{yday_gain:,} day-on-day)</i>"
        lines.append(line)

    streak = current_streak(daily)
    if streak:
        lines.append(f"🔥 Streak: <b>{streak} day{'s' if streak != 1 else ''}</b> of gains")

    wk = gain_last_n_days(daily, 7)
    if wk is not None:
        lines.append(f"📈 Last 7 days: <b>+{wk:,}</b> total")

    e = snapshot.get("energy", {})
    n = snapshot.get("nerve", {})
    reg = snapshot.get("regen", {})
    e_full = tw.seconds_to_full(e.get("current", 0), e.get("maximum", 0), reg.get("energy", {}))
    n_full = tw.seconds_to_full(n.get("current", 0), n.get("maximum", 0), reg.get("nerve", {}))
    e_tail = "full" if (e.get("maximum") and e.get("current", 0) >= e.get("maximum")) else tw.fmt_duration_compact(e_full)
    n_tail = "full" if (n.get("maximum") and n.get("current", 0) >= n.get("maximum")) else tw.fmt_duration_compact(n_full)
    erow = f"⚡ Energy {e.get('current',0)}/{e.get('maximum',0)}  {e_tail}"
    nrow = f"🔴 Nerve  {n.get('current',0)}/{n.get('maximum',0)}  {n_tail}"
    lines += ["", "<pre>" + erow + "\n" + nrow + "</pre>"]

    lines += ["", f"<i>“{line_of_the_day(local)}”</i>"]
    return "\n".join(lines)


def read_history(path: str = "history.csv") -> list[dict]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Torn Watch — daily digest.")
    p.add_argument("--dry-run", action="store_true", help="Print instead of sending.")
    args = p.parse_args(argv)

    try:
        tz_offset = int(os.environ.get("TZ_OFFSET_HOURS") or DEFAULT_TZ_OFFSET)
    except ValueError:
        tz_offset = DEFAULT_TZ_OFFSET

    api_key = os.environ.get("TORN_API_KEY")
    if not api_key:
        print("Missing env var: TORN_API_KEY", file=sys.stderr)
        return 2

    try:
        raw = tw.make_torn_fetcher(api_key, "bars,battlestats")("")
    except tw.TornApiError as e:
        print(f"Torn API error: {e}", file=sys.stderr)
        return 3
    snapshot = tw.parse_snapshot(raw)
    stats = tier2.parse_battlestats(raw)
    daily = daily_last_totals(read_history(), tz_offset)
    msg = format_digest(snapshot, stats, daily, datetime.now(timezone.utc), tz_offset)

    if args.dry_run:
        print(msg)
        return 0

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2
    tw.make_telegram_notifier(bot_token, chat_id)(msg)
    print("Digest sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
