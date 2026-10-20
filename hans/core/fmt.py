"""Small formatting helpers used everywhere."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def fmt_duration(seconds: Optional[int]) -> str:
    """'42m', '1h 05m', '2h', '<1m', 'now', '?'."""
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return "<1m"
    mins = int(seconds) // 60
    if mins < 60:
        return f"{mins}m"
    hours, rem = divmod(mins, 60)
    return f"{hours}h" if rem == 0 else f"{hours}h {rem:02d}m"


def fmt_duration_compact(seconds: Optional[int]) -> str:
    """No spaces, for aligned tables: '4h10m', '35m', 'now'."""
    if seconds is None:
        return "?"
    if seconds <= 0:
        return "now"
    if seconds < 60:
        return "<1m"
    mins = int(seconds) // 60
    if mins < 60:
        return f"{mins}m"
    hours, rem = divmod(mins, 60)
    return f"{hours}h" if rem == 0 else f"{hours}h{rem:02d}m"


def progress_bar(current: int, maximum: int, width: int = 5) -> str:
    """▰▰▰▱▱"""
    if maximum <= 0:
        return "▱" * width
    frac = max(0.0, min(1.0, current / maximum))
    filled = int(round(frac * width))
    return "▰" * filled + "▱" * (width - filled)


def html_escape(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def local_dt(epoch: float, tz_offset: int) -> datetime:
    return datetime.fromtimestamp(epoch, timezone.utc) + timedelta(hours=tz_offset)


def clock(epoch: float, tz_offset: int) -> str:
    """Local HH:MM for an epoch."""
    return f"{local_dt(epoch, tz_offset):%H:%M}"
