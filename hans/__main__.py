"""python -m hans run              run the bot (what GitHub Actions does)
   python -m hans log-stats PATH   append battle stats to the CSV and exit
   python -m hans check            print your live Torn payload + parsed snapshot
   python -m hans demo             print sample screens/alerts (no keys needed)"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone

from .config import Config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hans", description="Hans — read-only Torn assistant")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--seconds", type=int, default=None)
    ls = sub.add_parser("log-stats")
    ls.add_argument("path")
    sub.add_parser("check")
    sub.add_parser("demo")
    args = p.parse_args(argv)
    cfg = Config.from_env()

    if args.cmd == "demo":
        return _demo(cfg)

    if args.cmd in ("check", "log-stats"):
        if not cfg.api_key:
            print("Missing env var: TORN_API_KEY", file=sys.stderr)
            return 2
        from .core import bars, stats
        from .net.torn import Torn, TornApiError
        try:
            raw = Torn(cfg.api_key).user(
                "battlestats,bars" if args.cmd == "log-stats" else
                "bars,cooldowns,travel,profile,battlestats")
        except TornApiError as e:
            print(e, file=sys.stderr)
            return 3
        if args.cmd == "check":
            print(json.dumps(raw, indent=2))
            print(json.dumps(bars.parse_snapshot(raw), indent=2))
            return 0
        st = bars.parse_battlestats(raw)
        nnb = bars._to_int((raw.get("nerve") or {}).get("maximum"))
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        stats.append_history(args.path, ts, st, nnb)
        print(f"[stats] {ts} total={st['total']} nerve_bar={nnb} -> {args.path}")
        return 0

    missing = cfg.missing()
    if missing:
        print(f"Missing env vars: {', '.join(missing)}", file=sys.stderr)
        return 2
    from . import app
    from .context import Ctx
    from .net.feeds import Feeds
    from .net.telegram import Telegram
    from .net.torn import Torn
    from .store import Store
    store = Store(cfg.state_path).load()
    ctx = Ctx(cfg, store, Telegram(cfg.bot_token, cfg.chat_id), Torn(cfg.api_key), Feeds())
    app.run(ctx, args.seconds or cfg.run_seconds)
    return 0


def _demo(cfg: Config) -> int:
    """Render the main screens with sample data so you can eyeball them."""
    from . import settings as S
    from .core import bars
    from .core.messages import format_message, preview_samples
    from .ui import views as V
    now = time.time()
    snap = bars.parse_snapshot({
        "energy": {"current": 30, "maximum": 150, "increment": 5, "interval": 600,
                   "ticktime": 300, "fulltime": 11700},
        "nerve": {"current": 25, "maximum": 25},
        "happy": {"current": 4200, "maximum": 5000, "increment": 5, "interval": 900,
                  "ticktime": 60, "fulltime": 48000},
        "life": {"current": 300, "maximum": 400},
        "cooldowns": {"drug": 1800},
        "travel": {"time_left": 900, "destination": "China"},
    })
    st = S.with_defaults({})
    print(V.status(snap, now, cfg.tz_offset), "\n---")
    print(V.next_full(snap, now, cfg.tz_offset), "\n---")
    plan = {"code": "chi", "name": "China", "item": "Panda Plushie", "qty": 812,
            "cost": 400, "restock": now + 1500, "empties": now + 600, "basis": "selling",
            "delay": 900, "delay_learned": True, "cycle": 2820, "flight": 13020,
            "flight_n": 4, "depart": now + 180, "cycles_ahead": 4, "error": 2300}
    print(V.travel_screen([plan], st, snap, now, cfg.tz_offset, True), "\n---")
    print(V.route_detail(plan, "chi", True, now, cfg.tz_offset), "\n---")
    print(V.oc_screen({"ready": now + 15 * 3600, "source": "auto",
                       "fired": [86400]}, st, now, cfg.tz_offset), "\n---")
    print(format_message(preview_samples(now), now))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
