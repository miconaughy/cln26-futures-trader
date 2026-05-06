# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## What this project does

A single-file Python script (`trader.py`) that polls Grok (xAI) every 5 minutes with a configurable prompt, parses a buy/no-buy signal (1 or 0) from the response, and executes market orders for a crude oil futures contract (`/CLN26`) via Interactive Brokers (IBKR) using the `ib_async` library.

**Note:** Schwab's API does not support futures trading and is not an option for this project.

## Prerequisites

IB Gateway (or TWS) must be running with API access enabled before starting the trader:
1. Download IB Gateway: https://www.interactivebrokers.com/en/trading/ibgateway-stable.php
2. Log in with your IBKR credentials
3. Enable API: **Configure → API → Settings → Enable ActiveX and Socket Clients**
4. Paper trading port: **4002** | Live trading port: **4001**

## Commands

```bash
# Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Test IB Gateway connection and validate the futures contract
python3 first_login.py

# Run the trader
python3 trader.py
```

## Key configuration

All user-editable values live in the **OPEN VARIABLES** block at the top of `trader.py`:

| Variable | Purpose |
|---|---|
| `PROMPT` | Natural-language question sent to Grok each cycle |
| `GROK_API_KEY` | xAI API key (or set `GROK_API_KEY` env var) |
| `IB_PORT` | `7497` for paper trading, `7496` for live |
| `IB_CLIENT_ID` | IBKR API client ID (must be unique per simultaneous connection) |
| `IB_CONTRACT_EXPIRY` | Contract month as `YYYYMM` (e.g. `202607` for July 2026) |
| `ORDER_QUANTITY` | Contracts per buy signal |
| `ALERT_EMAIL_TO` | Where error alerts are sent |
| `POLL_INTERVAL_SECONDS` | Cycle frequency (default 300) |

## Architecture

```
trader.py
  └── main()               connects to IB Gateway, then loops every POLL_INTERVAL_SECONDS
       └── run_cycle()
            ├── query_grok()          POST to https://api.x.ai/v1 via openai SDK
            │                         parses the last standalone 0 or 1 from the response
            ├── get_open_position()   checks ib.positions() for open CL contracts
            ├── execute_buy()         qualifies contract, places MarketOrder("BUY", qty)
            ├── execute_sell(qty)     qualifies contract, places MarketOrder("SELL", qty)
            └── send_alert()          smtplib over TLS to SMTP_SERVER on error
```

**Decision × position logic:**

| Decision | Open position? | Action |
|---|---|---|
| 1 (buy) | No | Place buy order |
| 1 (buy) | Yes | Hold — no duplicate buy |
| 0 (no buy) | Yes | Place sell order (closes position) |
| 0 (no buy) | No | Do nothing |

**IBKR connection:** A single `IB()` instance (`_ib`) is created at module level and connected at startup. `get_ib()` auto-reconnects if the session drops between cycles.

**Contract:** `/CLN26` maps to IBKR `Future(symbol='CL', lastTradeDateOrContractMonth='202607', exchange='NYMEX', currency='USD')`. Update `IB_CONTRACT_EXPIRY` when rolling to the next contract month.

**Grok integration:** Uses the `openai` Python SDK pointed at `https://api.x.ai/v1`. No xAI-specific SDK needed.

**Decision parsing:** `re.findall(r"\b([01])\b", text)` — takes the last standalone `0` or `1` in Grok's response. Defaults to `0` if none found.

## Dependencies

- `openai` — Grok API calls (OpenAI-compatible endpoint)
- `ib_insync` — IBKR API (widely used, stable, identical API to the ib_async fork)

## Important caveats

- IB Gateway must be running and API-enabled before `trader.py` starts.
- IB Gateway sessions time out approximately every 24 hours; configure auto-restart in IB Gateway settings for unattended operation.
- Grok web search (required for the default prompt to access current news) is only available on paid xAI plans.
- `trader.log` accumulates indefinitely — rotate or truncate periodically.
- When the /CLN26 contract expires, update `IB_CONTRACT_EXPIRY` in the OPEN VARIABLES block to the next active month.
