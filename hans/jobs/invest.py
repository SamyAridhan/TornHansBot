"""One-time nudge: when your cash (on hand + bank + vault + Cayman) passes a
threshold, remind you it's time to start buying stock benefit blocks."""

from __future__ import annotations

from datetime import datetime, timezone

from .. import settings as S
from ..core import bars

THRESHOLD = 75_000_000          # spare-cash level where a first block makes sense
CHECK_EVERY = 6 * 3600
RETRY_ON_ERROR = 24 * 3600

MESSAGE = (
    "📈 <b>Time to look at stocks</b>\n"
    "You've got about <b>${total:,}</b> in cash. Keep what you need for "
    "travel + Xanax, then start with one benefit block:\n"
    "1. <b>EVL</b> (Evil Ducks) — 100k shares → 1,000 happy / week\n"
    "2. <b>WSU</b> — 1M shares → −10% course time\n"
    "3. <b>MCS</b> (Mc Smoogle) — 350k shares → 100 energy / week\n"
    "Buy on a dip, straight from the bank (don't carry the cash)."
)


def _int(v) -> int:
    try:
        return int(float(v or 0))
    except (TypeError, ValueError):
        return 0


def cash_total(raw: dict) -> int:
    raw = raw or {}
    bank = raw.get("city_bank") or {}
    return (_int(raw.get("money_onhand")) + _int(bank.get("amount") if isinstance(bank, dict) else bank)
            + _int(raw.get("vault_amount")) + _int(raw.get("cayman_bank")))


def run(ctx) -> float:
    st = ctx.store["invest"]
    if st.get("reminded"):
        return RETRY_ON_ERROR
    now = ctx.now()
    try:
        raw = ctx.torn.user("money")
    except Exception as e:  # noqa: BLE001 — key may lack access; never crash the loop
        ctx.log(f"invest: money read failed ({e})")
        return RETRY_ON_ERROR
    total = cash_total(raw)
    st["last_total"], st["checked"] = total, int(now)
    if total < THRESHOLD:
        return CHECK_EVERY
    if bars.in_quiet_hours(datetime.fromtimestamp(now, timezone.utc),
                           S.alert_config(ctx.settings), ctx.tz):
        return 1800
    ctx.send(MESSAGE.format(total=total))
    st["reminded"] = int(now)
    return RETRY_ON_ERROR
