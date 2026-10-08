#!/usr/bin/env python3
"""
Torn Watch — weekly stat-growth chart.

Renders your battle-stat history (history.csv) to a PNG and sends it to
Telegram as a photo with a short caption. Read-only. Run weekly from
chart.yml, or manually: `python3 chart.py` (sends) / `--out foo.png` (saves).

matplotlib is the only non-stdlib dependency, installed in the workflow.
"""

from __future__ import annotations

import csv
import io
import json
import os
import sys
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Optional

TELEGRAM_API_BASE = "https://api.telegram.org"


# --------------------------------------------------------------------------
# Pure data loading
# --------------------------------------------------------------------------
def load_series(path: str = "history.csv") -> list[dict]:
    """Parse history.csv into sorted rows of {dt, total, strength, ...}."""
    if not os.path.exists(path):
        return []
    out = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    dt = datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    out.append({
                        "dt": dt,
                        "total": int(r.get("total", 0)),
                        "strength": int(r.get("strength", 0)),
                        "defense": int(r.get("defense", 0)),
                        "speed": int(r.get("speed", 0)),
                        "dexterity": int(r.get("dexterity", 0)),
                    })
                except (KeyError, ValueError, TypeError):
                    continue
    except (OSError, csv.Error):
        return []
    return sorted(out, key=lambda x: x["dt"])


def weekly_gain(series: list[dict]) -> Optional[int]:
    """Total gained over the last 7 days of data, or None if too little."""
    if len(series) < 2:
        return None
    latest = series[-1]
    cutoff = latest["dt"].timestamp() - 7 * 86400
    base = series[0]
    for row in series:
        if row["dt"].timestamp() <= cutoff:
            base = row
        else:
            break
    return latest["total"] - base["total"]


# --------------------------------------------------------------------------
# Rendering (matplotlib)
# --------------------------------------------------------------------------
def render_png(series: list[dict]) -> bytes:
    """Dark, legible growth chart matching the dashboard's look."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.dates as mdates

    BG, FG, GRID = "#0f1420", "#e6e6e6", "#2a2f3a"
    ACCENT = "#5aa9ff"
    SUB = {"strength": "#ff7a7a", "defense": "#7ad1ff",
           "speed": "#8affc1", "dexterity": "#ffd27a"}

    xs = [r["dt"] for r in series]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    fig.patch.set_facecolor(BG)
    ax.set_facecolor(BG)

    for key, color in SUB.items():
        ax.plot(xs, [r[key] for r in series], color=color, linewidth=1.2,
                alpha=0.8, label=key.capitalize())
    ax.plot(xs, [r["total"] for r in series], color=ACCENT, linewidth=2.6,
            label="Total", zorder=5)

    ax.set_title("Hans's Ledger — stat growth", color=FG, fontsize=14, pad=12)
    ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
    for spine in ax.spines.values():
        spine.set_color(GRID)
    ax.tick_params(colors=FG, labelsize=8)
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{int(v):,}"))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b"))
    fig.autofmt_xdate(rotation=0, ha="center")
    leg = ax.legend(facecolor=BG, edgecolor=GRID, labelcolor=FG,
                    fontsize=8, loc="upper left", framealpha=0.6)
    for t in leg.get_texts():
        t.set_color(FG)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


# --------------------------------------------------------------------------
# Telegram photo upload (multipart/form-data, stdlib only)
# --------------------------------------------------------------------------
def _multipart(fields: dict, file_field: str, filename: str, file_bytes: bytes):
    boundary = f"----tornwatch{uuid.uuid4().hex}"
    nl = b"\r\n"
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(b"--" + boundary.encode() + nl)
        out.write(f'Content-Disposition: form-data; name="{k}"'.encode() + nl + nl)
        out.write(str(v).encode() + nl)
    out.write(b"--" + boundary.encode() + nl)
    out.write(f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"'.encode() + nl)
    out.write(b"Content-Type: image/png" + nl + nl)
    out.write(file_bytes + nl)
    out.write(b"--" + boundary.encode() + b"--" + nl)
    return out.getvalue(), boundary


def send_photo(bot_token: str, chat_id: str, png: bytes, caption: str) -> None:
    body, boundary = _multipart(
        {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"},
        "photo", "growth.png", png)
    req = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendPhoto",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "torn-watch-chart/1.0"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        resp.read()


def send_text(bot_token: str, chat_id: str, text: str) -> None:
    data = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage", data=data,
        headers={"Content-Type": "application/json", "User-Agent": "torn-watch-chart/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def send_chart(bot_token: str, chat_id: str) -> bool:
    """Render and send the weekly ledger chart. Returns True on success.

    Importable so the always-on poller can fire the chart on a fixed
    clock. Falls back to a text message when there's too little history.
    """
    series = load_series()
    if len(series) < 2:
        send_text(bot_token, chat_id,
                  "📈 Not enough stat history yet for a chart — "
                  "give it a few days of logging.")
        return True
    png = render_png(series)
    gain = weekly_gain(series)
    latest = series[-1]["total"]
    cap = f"📈 <b>Weekly ledger</b> — total <b>{latest:,}</b>"
    if gain is not None:
        cap += f"  (<b>+{gain:,}</b> in 7 days)"
    send_photo(bot_token, chat_id, png, cap)
    return True


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Torn Watch — weekly stat chart.")
    p.add_argument("--out", metavar="PATH", default=None,
                   help="Save the PNG to PATH instead of sending to Telegram.")
    args = p.parse_args(argv)

    series = load_series()
    if len(series) < 2:
        msg = "📈 Not enough stat history yet for a chart — give it a few days of logging."
        if args.out:
            print(msg)
            return 0
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        if bot_token and chat_id:
            send_text(bot_token, chat_id, msg)
        print(msg)
        return 0

    png = render_png(series)
    if args.out:
        with open(args.out, "wb") as f:
            f.write(png)
        print(f"Saved {len(png):,} bytes -> {args.out}")
        return 0

    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not (bot_token and chat_id):
        print("Missing TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        return 2

    gain = weekly_gain(series)
    latest = series[-1]["total"]
    cap = f"📈 <b>Weekly ledger</b> — total <b>{latest:,}</b>"
    if gain is not None:
        cap += f"  (<b>+{gain:,}</b> in 7 days)"
    send_photo(bot_token, chat_id, png, cap)
    print("Chart sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
