# Hans — your Torn assistant on Telegram

Hans **reads** your own Torn account through the official API and messages you
on Telegram when something is worth acting on. **You** open Torn and play — Hans
never takes a game action, which keeps it within Torn's rules.

Runs free, 24/7, on GitHub Actions. No server.

## Using it

Type **/menu** (or tap the pinned message). Everything is a button:

| Screen | What it does |
|---|---|
| 🏠 **Home** (pinned, auto-refreshing) | Energy / nerve / happy / life with time-to-full, cooldowns, travel, next "leave now", OC time |
| ✈️ **Travel** | Live stock, next restock and **when to leave** per country. Tap a country → switch its *leave now* alert on/off |
| ⚔️ **OC** | Your OC ready time (read from the faction API, or set with one tap). Reminders 24h / 12h / 8h before |
| 💪 **Stats** | Battle stats + gain; send the digest or growth chart now |
| ⚙️ **Settings** | Every alert on/off with one tap; landing-buzz lead, digest time, quiet hours |

What arrives on its own:
- **Bar alerts** — energy reaches your mark, energy/nerve about to cap or full, cooldowns done, hospital/jail in & out, stat milestones. Routine ones hold back during quiet hours (default 01:00–08:00).
- **Landing buzz** — a ping every 2 s starting 60 s before you land abroad, until you tap ✅ (the buzz messages then delete themselves). Trips home don't buzz.
- **Leave now** — for watched countries, at the exact minute to take off so you land ~2 min after a restock.
- **OC reminders**, **morning digest** (08:30) and a **Sunday growth chart**.

Typed shortcuts still work: `/status`, `/stock china`, `/price 206`, `/oc in 12h`, `/oc 21/10 14:00`, `/watch mexico on`, `/testland`, `/help`.

## Setup (one time)

1. **Torn API key** — Torn → Settings → API Keys → a *Limited* key.
2. **Telegram bot** — message @BotFather, `/newbot`, copy the token, then send your bot "hi".
3. **Chat id** — open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `"chat":{"id":…}`.
4. In the GitHub repo → Settings → Secrets and variables → Actions, add secrets
   `TORN_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, and optionally the
   variable `TZ_OFFSET_HOURS` (default 8).
5. Actions → **hans** → Run workflow. Hans says hello and pins the dashboard.

## How it's built

```
hans/
  config.py     secrets from env + engine timings
  settings.py   every option you can change in Telegram (with defaults)
  store.py      one state file, hans_state.json (atomic saves)
  net/          Torn API, Telegram API, community travel feeds
  core/         pure logic: bars & alerts, travel math, OC, stats, chart
  jobs/         bars · landing · depart · oc · schedule · pin
  ui/           screens, buttons, router
  app.py        one loop: run due jobs, long-poll Telegram until the next is due
tests/          103 tests incl. a simulated flight + buzz
```

- **One bot, one workflow** (`hans.yml`): each run lasts ~5h40m; an hourly
  request queues the next run so it takes over immediately. State is carried in
  the Actions cache.
- **stats.yml** logs battle stats to `history.csv` every 6 h (used by the
  digest, the chart and `index.html`).
- Travel stock comes from Prombot (YATA fallback); restock timing from the
  community restock model. Flight times are learned from your own trips.

Run locally:

```bash
python3 -m hans demo                      # sample screens, no keys
TORN_API_KEY=… python3 -m hans check      # your raw payload + parsed view
python3 -m unittest discover -s tests -t .
```
