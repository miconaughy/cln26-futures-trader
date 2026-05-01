# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does

A single-file Python script (`trader.py`) that polls Grok (xAI) every 5 minutes with a configurable prompt, parses a buy/no-buy signal (1 or 0) from the response, and executes a market buy order for a crude oil futures contract (`/CLN26`) via the Schwab API (ThinkOrSwim). Errors default to 0 (no action) and trigger an email alert.

## Commands

```bash
# Install dependencies
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# One-time Schwab OAuth login (writes schwab_token.json)
python3 first_login.py

# Run the trader
python3 trader.py
```

## Key configuration

All user-editable values live in the **OPEN VARIABLES** block at the top of `trader.py` (roughly lines 20–50). The most important ones:

| Variable | Purpose |
|---|---|
| `PROMPT` | The natural-language question sent to Grok each cycle |
| `GROK_API_KEY` | xAI API key (or set `GROK_API_KEY` env var) |
| `SCHWAB_APP_KEY / SECRET` | Schwab developer app credentials |
| `SCHWAB_ACCOUNT_HASH` | Hash of the futures-enabled brokerage account |
| `FUTURES_SYMBOL` | Contract to trade (default `/CLN26`) |
| `ORDER_QUANTITY` | Contracts per buy signal |
| `ALERT_EMAIL_TO` | Where error alerts are sent |
| `POLL_INTERVAL_SECONDS` | Cycle frequency (default 300) |

Credentials can be set as environment variables instead of editing the file — variable names match exactly.

## Architecture

```
trader.py
  └── main()               infinite loop, sleeps POLL_INTERVAL_SECONDS between cycles
       └── run_cycle()
            ├── query_grok()          POST to https://api.x.ai/v1 via openai SDK
            │                         parses the last standalone 0 or 1 from the response
            ├── get_open_position()   GET account positions, returns long contract count
            ├── execute_buy()         market buy ORDER_QUANTITY contracts
            ├── execute_sell(qty)     market sell qty contracts (closes the position)
            └── send_alert()          smtplib over TLS to SMTP_SERVER on error
```

**Decision × position logic in `run_cycle()`:**

| Decision | Open position? | Action |
|---|---|---|
| 1 (buy) | No | Place buy order |
| 1 (buy) | Yes | Hold — no duplicate buy |
| 0 (no buy) | Yes | Place sell order (closes position) |
| 0 (no buy) | No | Do nothing |

If the position check itself fails, the cycle is skipped entirely (no buy or sell) and an alert is sent.

**Grok integration**: uses the `openai` Python SDK pointed at `https://api.x.ai/v1`. No xAI-specific SDK needed.

**Schwab auth**: `schwab.auth.client_from_token_file()` reads `schwab_token.json` and handles OAuth token refresh transparently. `first_login.py` must be run once to create that file via browser-based login.

**Decision parsing**: `re.findall(r"\b([01])\b", text)` — takes the last standalone `0` or `1` in Grok's response. If none found, defaults to `0`.

## Dependencies

- `openai` — Grok API calls (OpenAI-compatible endpoint)
- `schwab-py` — Schwab/ThinkOrSwim brokerage API

## Important caveats

- The Schwab account must be approved for futures trading; the order builder in `execute_buy()` uses `EquityInstruction.BUY` as a starting point but may need adjustment to a futures-specific instruction type depending on account configuration.
- Grok web search (required for the default prompt to access current news) is only available on paid xAI plans.
- `trader.log` accumulates indefinitely — rotate or truncate periodically.
