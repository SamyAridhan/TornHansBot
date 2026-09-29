# Deploy Torn Watch tonight — the easy way (10 minutes)

No git, no `.env`, no command line, nothing installed. All in your browser, on your
**home** computer. You'll paste your 3 saved values into GitHub's own web pages.

You need the 3 values you already collected:
- **TORN_API_KEY**
- **TELEGRAM_BOT_TOKEN**
- **TELEGRAM_CHAT_ID**

And the file: **torn-watch.zip** (unzip it first — you'll get a `torn-watch` folder).

---

## Step 1 — GitHub account
If you don't have one: go to github.com → Sign up. If you do, sign in. (2 min)

## Step 2 — Make a private repo
- Top-right **＋** → **New repository**
- Name it anything (e.g. `torn-watch`)
- Tick **Private**
- Click **Create repository**

## Step 3 — Put the files in (drag & drop, no git)
- On the new empty repo page, click the link **"uploading an existing file"**
  (or go to **Add file → Upload files**)
- Open your unzipped `torn-watch` folder, select **all** the files inside it, and
  **drag them into the browser** upload box.
  - Make sure you also grab the `.github` folder (it holds the schedule). If drag
    won't include it, that's fine — see the note at the bottom.
- Scroll down, click **Commit changes**.

## Step 4 — Add your 3 secrets
- In the repo, click **Settings** (top bar)
- Left sidebar → **Secrets and variables** → **Actions**
- Click **New repository secret**, then add each of these (one at a time):

  | Name (type exactly) | Secret (paste your value) |
  |---|---|
  | `TORN_API_KEY` | your Torn limited key |
  | `TELEGRAM_BOT_TOKEN` | your bot token |
  | `TELEGRAM_CHAT_ID` | your chat id number |

  Click **Add secret** after each. Names must match exactly (all caps, underscores).

## Step 5 — Turn on Actions & run it once
- Click the **Actions** tab (top bar)
- If it asks to enable workflows, click **"I understand… enable"**
- Click **torn-watch** on the left → **Run workflow** button → **Run workflow**
- Wait ~30 sec, refresh. A green ✓ = it ran.

## Step 6 — Confirm it's alive
- The **first** run is silent on purpose (it just learns your starting state).
- After that, it checks every 15 minutes and messages your Telegram bot when
  energy/nerve hits full, a cooldown ends, or you land from travel.
- To force a test message: spend some energy in Torn, wait for it to refill, and
  within 15 min of it hitting full you'll get a ping.

That's it. It now runs free, 24/7, with your laptop off.

---

## If something's off
- **No messages ever:** first run is always silent — that's normal. Also double-check
  the 3 secret **names** are spelled exactly right.
- **Want to check the connection now:** the repo has a `--check` mode. Easiest is to
  just tell me and we'll run it together at the laptop — it prints what Torn actually
  returns so we confirm the fields.
- **Red ✗ on a run:** open the failed run, copy the error text, paste it to me.
- **Couldn't upload the `.github` folder by drag:** in the repo click
  **Add file → Create new file**, type `.github/workflows/poll.yml` as the name
  (the slashes make the folders), paste the contents of that file from the zip, commit.
- **Stops after ~2 months:** GitHub pauses idle scheduled workflows — just open the
  repo and hit **Run workflow** once to wake it, or push any small change.

## Reminder
Keep your 3 values private — they live only in GitHub Secrets (encrypted) and your
own notes. Never paste them into the code files or a public place.
