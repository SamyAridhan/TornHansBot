"""Environment + tuning constants. Everything user-facing lives in settings.py
(changeable from Telegram); this file is only secrets and engine knobs."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name) or default)
    except ValueError:
        return default


@dataclass
class Config:
    api_key: str = ""
    bot_token: str = ""
    chat_id: str = ""
    tz_offset: int = 8              # hours from UTC (Asia/Singapore = 8)
    run_seconds: int = 20400        # one GitHub Actions run (~5h40m)
    state_path: str = "hans_state.json"
    history_path: str = "history.csv"
    log_dir: str = "."

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            api_key=os.environ.get("TORN_API_KEY", ""),
            bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            tz_offset=_env_int("TZ_OFFSET_HOURS", 8),
            run_seconds=_env_int("HANS_RUN_SECONDS", 20400),
            state_path=os.environ.get("HANS_STATE", "hans_state.json"),
            history_path=os.environ.get("HANS_HISTORY", "history.csv"),
            log_dir=os.environ.get("HANS_LOG_DIR", "."),
        )

    def missing(self) -> list[str]:
        return [n for n, v in (("TORN_API_KEY", self.api_key),
                               ("TELEGRAM_BOT_TOKEN", self.bot_token),
                               ("TELEGRAM_CHAT_ID", self.chat_id)) if not v]


# --- Engine timing (seconds) ---
BARS_POLL_MIN = 30          # bar/travel snapshot: never faster than this
BARS_POLL_MAX = 120         # ...and never slower (keeps the pin + landing fresh)
BUZZ_INTERVAL = 2           # landing buzz: one ping every N seconds
BUZZ_AFTER_LANDING = 60     # keep buzzing this long after touchdown
BUZZ_DEMO_SECONDS = 60      # /testland demo length
STOCK_POLL = 60             # read live foreign stock this often
OC_POLL = 300               # re-read the OC ready time this often
SCHEDULE_POLL = 60          # digest/chart clock check
PIN_REFRESH = 300           # pinned dashboard edit cadence
PIN_IDLE_RESET = 600        # pinned msg returns to Home after this idle time
LONG_POLL_MAX = 30          # Telegram getUpdates max block
STOCK_CACHE = 45            # community stock feed cache
MODEL_CACHE = 1800          # restock model cache (it changes slowly)
LAND_AFTER_RESTOCK = 120    # aim to land this long after a restock
