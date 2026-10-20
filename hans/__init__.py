"""Hans — a read-only Torn → Telegram assistant.

Layout:
  config.py    environment + tuning constants
  store.py     the ONE state file (hans_state.json) + legacy migration
  settings.py  everything you can change from Telegram, with defaults
  net/         Torn API, Telegram API, community travel feeds
  core/        pure logic (no network): bars, alerts, travel, OC, stats, chart
  jobs/        background watchers (bars, landing, depart, OC, schedule, pin)
  ui/          screens, buttons, and the router that answers taps/commands
  app.py       the single loop that runs it all

Hans only READS your data and messages you. It never takes a game action.
"""

__version__ = "2.0"
