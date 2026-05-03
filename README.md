# CLN26 Crude Oil Futures Automated Trader

> **Note:** The Schwab Trader API does not support futures order placement (only EQUITY and OPTION). This codebase is preserved as a reference implementation and starting point for platforms that do support futures (IBKR, tastytrade, TradeStation, etc.). The order-placement functions in `trader.py` would need to be adapted to your broker's API.

Polls Grok (xAI) every 5 minutes for a buy/sell signal on `/CLN26` (Crude Oil futures) and executes trades automatically via the Schwab API.

**Trading logic:**
| Grok signal | Open position? | Action |
|---|---|---|
| Buy (1) | No | Place market buy |
| Buy (1) | Yes | Hold — no duplicate buy |
| No buy (0) | Yes | Close position with market sell |
| No buy (0) | No | Do nothing |

- One buy and one sell maximum per calendar day
- Errors default to no action and trigger an email alert
- A Textual TUI (`ui.py`) provides live log output, editable symbol/interval settings, and an in-app prompt editor

---

## Requirements

- Python 3.10+
- A **paid xAI account** (Grok web search required for the default prompt)
- A **Schwab brokerage account** (futures order support depends on your broker)
- A **Gmail account** with an App Password for alerts

---

## Setup

### 1. Install dependencies

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure credentials

```bash
cp .env.example .env
```

Open `.env` and fill in all values.

### 3. Schwab OAuth login (one time only)

Create a Schwab developer app at [developer.schwab.com](https://developer.schwab.com) with callback URL `https://127.0.0.1:8182`, then run:

```bash
python3 first_login.py
```

A browser will open — log in and paste the redirect URL back into the terminal. This writes `schwab_token.json`, which is reused and auto-refreshed from then on.

### 4. Get your account hash

```bash
python3 get_account_hash.py
```

Copy the `hashValue` for your account and paste it into `SCHWAB_ACCOUNT_HASH` in `.env`.

### 5. Run

```bash
# Headless (logs to trader.log)
python3 trader.py

# Terminal UI
python3 ui.py
```

---

## Configuration

All credentials go in `.env`. Trading parameters (`PROMPT`, `FUTURES_SYMBOL`, `ORDER_QUANTITY`, `POLL_INTERVAL_SECONDS`) are edited at the top of `trader.py` or live via the UI.

---

## Running in the background

```bash
nohup python3 trader.py &> trader.log &
echo $! > trader.pid       # save PID
kill $(cat trader.pid)     # stop later
```
