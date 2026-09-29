# Torn Watch

A **read-only** watcher for your own Torn account. It polls the official Torn API
and sends *you* a Telegram message when something worth acting on happens —
energy full, nerve full, a cooldown ends, you land from travel. Then **you** open
Torn and play.

It never performs a game action. Reading your own data via the official API is
allowed; automated *gameplay* is not, and this tool deliberately stays on the
allowed side of that line. You always click the buttons yourself.

- **Zero dependencies** — standard library only, so it runs anywhere.
- **Edge-triggered alerts** — one message when energy *becomes* full, not one
  every poll while it stays full.
- **Runs free** on GitHub Actions (no server, no credit card).

---

## What you need to do (one-time, ~5 minutes)

### 1. Get a Torn API key
Torn → **Settings → API Keys** → create a **Limited** (minimal access) key.
That's enough to read bars/cooldowns/travel. Copy it.

### 2. Make a Telegram bot
- In Telegram, message **@BotFather** → send `/newbot` → follow prompts.
- It gives you a **bot token** like `123456789:AA...`. Copy it.
- **Send your new bot any message** (e.g. "hi") — a bot can't message you until
  you've started a chat with it.

### 3. Get your Telegram chat id
Open this URL in a browser (paste your token in):
```
https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates
```
Find `"chat":{"id":123456789,...}` in the response — that number is your
`TELEGRAM_CHAT_ID`.

### 4. Confirm it works locally (optional but recommended)
```bash
python3 torn_watch.py --dry-run      # prints sample alerts, no keys needed
```
Then, with your real key set, confirm the API shape matches (no Telegram needed):
```bash
TORN_API_KEY=yourkey python3 torn_watch.py --check
```
This prints the raw payload next to the parsed snapshot. If the parsed side shows
`0` where the raw side has real numbers, a field name in `parse_snapshot()` needs
a tweak — tell me the raw output and I'll fix it. (This is the honest "verify the
API, don't assume it" step.)

---

## Deploy it free (GitHub Actions)

1. Create a new GitHub repo and push these files into it.
2. Repo → **Settings → Secrets and variables → Actions → New repository secret**,
   add three secrets:
   - `TORN_API_KEY`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
3. That's it. The workflow in `.github/workflows/poll.yml` runs every 15 minutes.
   Trigger a first run manually from the **Actions** tab → *torn-watch* → *Run workflow*.

**Note:** GitHub disables scheduled workflows after 60 days with no repo activity —
just push a commit occasionally, or run it manually, to keep it alive. State is
kept in the Actions cache (best-effort); a rare cache miss just re-baselines
silently, never spams.

### Prefer a rock-solid always-on box later?
The same script runs on any always-free VM (e.g. Oracle Cloud Always Free) via
cron, where `state.json` on disk persists perfectly:
```
*/10 * * * *  cd /path/to/torn-watch && TORN_API_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python3 torn_watch.py
```

---

## Configuring alerts

Edit `default_config()` in `torn_watch.py`:
- Turn individual alerts on/off under `"alerts"`.
- Set a custom threshold under `"thresholds"` (e.g. `"nerve": 24` to be pinged
  just before nerve overflows and wastes regen). `None` = alert at maximum.

`happy_full` is **off** by default (happy refills fast and is noisy). Everything
else on.

---

## Running the tests
```bash
python3 -m unittest -v test_torn_watch
```
22 tests cover parsing, the edge-trigger rules (no repeat while full, re-fire
after refill), cooldown/travel transitions, message formatting, and the
orchestration. They use injected fake network functions, so they never touch the
real API.

---

## How it's built (so it's easy to extend later)

- `parse_snapshot(raw)` — flattens the API payload defensively.
- `evaluate(prev, cur, config)` — **pure** function, returns `(events, new_state)`,
  edge-triggered against the previous snapshot.
- `run_once(config, fetcher, notifier, prev_state)` — one poll cycle; network
  functions are injected, which is what makes it testable.
- `make_torn_fetcher` / `make_telegram_notifier` — the real I/O.

**Adding more alerts** is just: pull more selections in the fetcher, add fields in
`parse_snapshot`, add rules in `evaluate`, and a line in `format_event`.

---

## Tier 2 (`tier2.py`) — built and tested, not yet live-wired

Three extra read-only modules, each unit-tested (`python3 -m unittest test_tier2`):

- **Market-price alerts** — ping when a watched item's cheapest listing drops
  to/below a target (e.g. a Metal Detector under 1M). Edge-triggered.
- **Open-OC-slot alerts** — ping when your faction opens an OC slot where your
  CPR clears a threshold worth taking.
- **Stats-over-time log** — append battle stats + NNB to a CSV each poll, to chart
  growth later.

**Status:** the *logic* is done and proven. What's deliberately **not** wired yet is
the live fetch for these — the Torn market and faction-OC endpoints vary by API
version, and (per this project's "verify, don't assume" rule) their exact field
names get confirmed against a real key first. Once confirmed, wiring each into the
poll loop is a few lines: fetch the selection → parse → call the `tier2.evaluate_*`
function → send `format_tier2_event`. The market/OC alerts also need a slightly
broader API key than the minimal one (faction access for OCs); the bars/cooldowns
watcher stays on the limited key.
