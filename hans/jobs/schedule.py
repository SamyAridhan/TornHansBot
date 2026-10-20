"""Morning digest + Sunday chart, fired by the local clock (not GitHub cron)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .. import config as C
from .. import settings as S
from ..core import bars, stats
from ..core.chart import render_png


def _week_key(d: datetime) -> str:
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def due_sends(now_local: datetime, config: dict, sched_state: dict) -> list[str]:
    due = []
    sch = config.get("schedule", {})
    d = sch.get("digest", {})
    if d.get("enabled") and (now_local.hour, now_local.minute) >= (d["hour"], d["minute"]) \
            and sched_state.get("digest_date") != now_local.date().isoformat():
        due.append("digest")
    c = sch.get("chart", {})
    if c.get("enabled") and now_local.weekday() == c.get("weekday", 6) \
            and (now_local.hour, now_local.minute) >= (c.get("hour", 9), c.get("minute", 0)) \
            and sched_state.get("chart_week") != _week_key(now_local):
        due.append("chart")
    return due


def send_digest(ctx) -> None:
    raw = ctx.torn.user("bars,battlestats")
    rows = stats.read_history(ctx.cfg.history_path)
    msg = stats.format_digest(bars.parse_snapshot(raw), bars.parse_battlestats(raw),
                              stats.daily_last_totals(rows, ctx.tz),
                              datetime.fromtimestamp(ctx.now(), timezone.utc), ctx.tz)
    ctx.send(msg)


def send_chart(ctx) -> None:
    series = stats.load_series(stats.read_history(ctx.cfg.history_path))
    if len(series) < 2:
        ctx.send("📈 Not enough stat history yet for a chart — give it a few days.")
        return
    gain = stats.weekly_gain(series)
    cap = f"📈 <b>Weekly ledger</b> — total <b>{series[-1]['total']:,}</b>"
    if gain is not None:
        cap += f"  (<b>+{gain:,}</b> in 7 days)"
    if not ctx.tg.photo_bytes(render_png(series), cap):
        ctx.send(cap + "\n<i>(chart image failed to upload)</i>")


def run(ctx) -> float:
    st = ctx.store["schedule"]
    local = datetime.fromtimestamp(ctx.now(), timezone.utc) + timedelta(hours=ctx.tz)
    if "digest_date" not in st and "chart_week" not in st:
        # first boot: don't backfill a stale send at a random hour
        st.update({"digest_date": local.date().isoformat(), "chart_week": _week_key(local)})
        return C.SCHEDULE_POLL
    due = due_sends(local, S.alert_config(ctx.settings), st)
    for which in due:                   # claim before sending: never loop-resend
        if which == "digest":
            st["digest_date"] = local.date().isoformat()
        else:
            st["chart_week"] = _week_key(local)
    for which in due:
        try:
            (send_digest if which == "digest" else send_chart)(ctx)
        except Exception as e:  # noqa: BLE001
            ctx.log(f"scheduled {which} failed: {e}")
    return C.SCHEDULE_POLL
