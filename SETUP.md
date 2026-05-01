# Setup Guide

## 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## 2. xAI (Grok) API key

1. Go to https://console.x.ai and sign in with your X account.
2. Navigate to **API Keys** → **Create API Key**.
3. Copy the key and either:
   - Set it as an environment variable: `export GROK_API_KEY="xai-..."`, or
   - Paste it directly into `GROK_API_KEY` in `trader.py`.

Grok with live web search is required for the prompt to access current news. Make sure your xAI plan includes web search access (currently available on paid tiers).

---

## 3. Schwab / ThinkOrSwim API

### 3a. Create a Schwab developer app

1. Go to https://developer.schwab.com and log in with your Schwab brokerage account.
2. Click **Create App**.
   - **App Name**: anything (e.g. "CLN26 Trader")
   - **Callback URL**: `https://127.0.0.1`  ← exact value required by schwab-py
3. Submit and wait for approval (usually same-day).
4. Copy your **App Key** and **App Secret** into `trader.py` (or set `SCHWAB_APP_KEY` / `SCHWAB_APP_SECRET` env vars).

### 3b. First-time OAuth login (run once)

Run this one-time script to authenticate and save your token:

```bash
python3 first_login.py
```

A browser window will open asking you to log in to Schwab. After login you'll be redirected to a `127.0.0.1` URL — copy the full URL from the browser address bar and paste it into the terminal prompt.

This writes `schwab_token.json`. After that, `trader.py` refreshes the token automatically.

### 3c. Find your account hash

After `first_login.py` succeeds, run:

```bash
python3 -c "
import schwab
c = schwab.auth.client_from_token_file('schwab_token.json', '<APP_KEY>', '<APP_SECRET>')
import json; print(json.dumps(c.get_account_numbers().json(), indent=2))
"
```

Copy the `hashValue` for your futures-enabled account and set `SCHWAB_ACCOUNT_HASH` in `trader.py`.

> **Important**: Your Schwab account must be approved for futures trading. Contact Schwab if `/CLN26` orders are rejected.

---

## 4. Gmail app password (for alerts)

> Skip if you use a different SMTP provider — update `SMTP_SERVER` / `SMTP_PORT` in `trader.py`.

1. Enable 2-Step Verification on your Google account.
2. Go to https://myaccount.google.com/apppasswords.
3. Create an app password named "Trader Alert".
4. Paste the 16-character password into `ALERT_EMAIL_PASSWORD` in `trader.py` (or set the `EMAIL_PASSWORD` env var).
5. Set `ALERT_EMAIL_FROM` to your Gmail address.

---

## 5. Run the trader

```bash
python3 trader.py
```

Logs are written to `trader.log` and the console simultaneously.

---

## 6. Run in the background (optional)

```bash
nohup python3 trader.py &> trader.log &
echo $! > trader.pid          # save PID to kill later
kill $(cat trader.pid)        # stop the process
```
