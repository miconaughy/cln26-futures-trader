#!/usr/bin/env python3
"""
Crude Oil Futures Automated Trader

Every POLL_INTERVAL_SECONDS the script:
  1. Sends PROMPT to Grok and parses a buy decision (1 = buy, 0 = no buy).
  2. Checks the account for an open position in FUTURES_SYMBOL.
  3. decision=1, no position  → place a market buy order.
     decision=1, position open → hold (no duplicate buy).
     decision=0, position open → close the position with a market sell.
     decision=0, no position  → do nothing.
  4. On any error → defaults to 0, waits for the next cycle, and sends an email alert.

All user-configurable values are set via a .env file (see .env.example).
PROMPT and trading parameters can also be edited directly in the OPEN VARIABLES
section below.
"""

import os
import re
import smtplib
import logging
import time
from datetime import datetime, date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv
from openai import OpenAI
import schwab

load_dotenv()

# ============================================================
# OPEN VARIABLES — Set credentials in .env; edit trading
# parameters here directly.
# ============================================================

# --- xAI / Grok ---
GROK_API_KEY = os.environ["GROK_API_KEY"]
GROK_MODEL   = "grok-3"

PROMPT = (
    'You are a seasoned futures trader that specializes within the commodity market - '
    'specifically crude oil futures. Using all of the most recent articles and publicly '
    'available information from the web, as well as market trends, make a determination '
    'to buy or not buy crude oil futures under the ticker symbol "/CLN26". '
    'You are specifically looking for 1 to 2 month outlooks for crude oil prices. '
    'Important constraint: this system executes at most one trade action per calendar day, '
    'and a buy and a sell cannot occur on the same calendar day. Only recommend buying if '
    'conditions strongly support a new long entry, and only recommend selling if conditions '
    'strongly support closing an existing position — each signal commits the day\'s single '
    'allowed trade. '
    'Provide that determination in word format, but also a "1" for yes and a "0" for no.'
)

# --- Schwab / ThinkOrSwim ---
SCHWAB_APP_KEY      = os.environ["SCHWAB_APP_KEY"]
SCHWAB_APP_SECRET   = os.environ["SCHWAB_APP_SECRET"]
SCHWAB_TOKEN_PATH   = "schwab_token.json"   # written on first OAuth login; refreshed automatically
SCHWAB_ACCOUNT_HASH = os.environ["SCHWAB_ACCOUNT_HASH"]

FUTURES_SYMBOL = "/CLN26"
ORDER_QUANTITY = 1          # contracts per buy signal

# --- Email alerts ---
ALERT_EMAIL_FROM     = os.environ["ALERT_EMAIL_FROM"]
ALERT_EMAIL_TO       = os.environ["ALERT_EMAIL_TO"]
ALERT_EMAIL_PASSWORD = os.environ["ALERT_EMAIL_PASSWORD"]
SMTP_SERVER          = os.environ.get("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT            = int(os.environ.get("SMTP_PORT", "587"))

# --- Timing ---
POLL_INTERVAL_SECONDS = 300   # 5 minutes

# ============================================================
# INTERNALS — No need to edit below this line
# ============================================================

log = logging.getLogger(__name__)

# Tracks the calendar date of the last buy and sell to enforce the one-trade-per-day rule.
last_buy_date:  date | None = None
last_sell_date: date | None = None


def setup_logging() -> None:
    """Configure logging to file + console. Called by main() and overridden by ui.py."""
    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(logging.FileHandler("trader.log"))
    root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)


# ---------- Grok ----------

def query_grok() -> tuple[int, str]:
    """Return (decision, full_response_text). Decision is 1 (buy) or 0 (no buy)."""
    now = datetime.now()
    date_context = (
        f"\n\nToday's date is {now.strftime('%A, %B %d, %Y')}. "
        f"Your analysis must be grounded primarily in information from {now.year} and {now.year - 1}. "
        f"Prioritize insights from today, this week, this month, and this quarter — in that order. "
        f"Recent geopolitical events, OPEC decisions, supply/demand data, and macroeconomic signals "
        f"from {now.year} should carry the most weight. Historical trends from prior years may inform "
        f"context but must not drive the recommendation."
    )
    client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=GROK_MODEL,
        messages=[{"role": "user", "content": PROMPT + date_context}],
    )
    text = response.choices[0].message.content.strip()
    digits = re.findall(r"\b([01])\b", text)
    decision = int(digits[-1]) if digits else 0
    return decision, text


# ---------- Email ----------

def send_alert(subject: str, body: str) -> None:
    try:
        msg = MIMEMultipart()
        msg["From"]    = ALERT_EMAIL_FROM
        msg["To"]      = ALERT_EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(ALERT_EMAIL_FROM, ALERT_EMAIL_PASSWORD)
            server.send_message(msg)
        log.info("Alert email sent to %s", ALERT_EMAIL_TO)
    except Exception as exc:
        log.error("Failed to send alert email: %s", exc)


# ---------- Schwab / ThinkOrSwim ----------

def get_schwab_client():
    """Return an authenticated Schwab client; refreshes the OAuth token automatically."""
    return schwab.auth.client_from_token_file(
        SCHWAB_TOKEN_PATH,
        SCHWAB_APP_KEY,
        SCHWAB_APP_SECRET,
    )


def get_open_position() -> int:
    """Return the number of long contracts currently held for FUTURES_SYMBOL, or 0 if none."""
    client = get_schwab_client()
    resp = client.get_account(SCHWAB_ACCOUNT_HASH, fields=[client.Account.Fields.POSITIONS])
    resp.raise_for_status()
    positions = resp.json().get("securitiesAccount", {}).get("positions", [])
    for pos in positions:
        if pos.get("instrument", {}).get("symbol") == FUTURES_SYMBOL:
            return int(pos.get("longQuantity", 0))
    return 0


def execute_buy() -> None:
    """Place a market buy-to-open order for FUTURES_SYMBOL."""
    client = get_schwab_client()
    order = {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": "BUY_TO_OPEN",
                "quantity": ORDER_QUANTITY,
                "instrument": {
                    "symbol": FUTURES_SYMBOL,
                    "assetType": "FUTURE",
                },
            }
        ],
    }
    resp = client.place_order(SCHWAB_ACCOUNT_HASH, order)
    resp.raise_for_status()
    log.info("Buy order placed: %s x%d", FUTURES_SYMBOL, ORDER_QUANTITY)


def execute_sell(quantity: int) -> None:
    """Place a market sell-to-close order to close the open FUTURES_SYMBOL position."""
    client = get_schwab_client()
    order = {
        "orderType": "MARKET",
        "session": "NORMAL",
        "duration": "DAY",
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [
            {
                "instruction": "SELL_TO_CLOSE",
                "quantity": quantity,
                "instrument": {
                    "symbol": FUTURES_SYMBOL,
                    "assetType": "FUTURE",
                },
            }
        ],
    }
    resp = client.place_order(SCHWAB_ACCOUNT_HASH, order)
    resp.raise_for_status()
    log.info("Sell order placed: %s x%d", FUTURES_SYMBOL, quantity)


# ---------- Main cycle ----------

def run_cycle() -> None:
    log.info("=== Cycle start ===")
    decision = 0

    # 1. Query Grok
    try:
        decision, response_text = query_grok()
        log.info("Grok says:\n%s", response_text)
        log.info("Parsed decision: %d", decision)
    except Exception as exc:
        log.error("Grok query failed: %s", exc)
        send_alert(
            subject="[Trader] Grok API error — defaulting to NO BUY",
            body=f"Error at {datetime.now()}\n\n{exc}",
        )
        decision = 0

    # 2. Check current position
    try:
        open_contracts = get_open_position()
        log.info("Open position: %d contract(s)", open_contracts)
    except Exception as exc:
        log.error("Failed to fetch position: %s", exc)
        send_alert(
            subject="[Trader] Position check failed — skipping cycle",
            body=f"Could not read account positions at {datetime.now()}\n\n{exc}",
        )
        return

    # 3. Enforce daily trade limit
    global last_buy_date, last_sell_date
    today = date.today()
    log.info(
        "Daily trade log — last buy: %s  last sell: %s",
        last_buy_date or "none", last_sell_date or "none",
    )

    # 4. Act based on decision × position state × daily limits
    if decision == 1 and open_contracts == 0:
        if last_buy_date == today:
            log.info("Daily limit reached — already bought today, skipping buy.")
        else:
            log.info("Decision BUY, no open position — placing buy order.")
            try:
                execute_buy()
                last_buy_date = today
            except Exception as exc:
                log.error("Buy order failed: %s", exc)
                send_alert(
                    subject="[Trader] Buy order failed",
                    body=f"Decision was BUY but order failed at {datetime.now()}\n\n{exc}",
                )
    elif decision == 1 and open_contracts > 0:
        log.info("Decision BUY, already holding %d contract(s) — holding.", open_contracts)
    elif decision == 0 and open_contracts > 0:
        if last_sell_date == today:
            log.info("Daily limit reached — already sold today, skipping sell.")
        elif last_buy_date == today:
            log.info("Daily limit reached — bought today, cannot sell on the same day.")
        else:
            log.info("Decision NO BUY, closing open position of %d contract(s).", open_contracts)
            try:
                execute_sell(open_contracts)
                last_sell_date = today
            except Exception as exc:
                log.error("Sell order failed: %s", exc)
                send_alert(
                    subject="[Trader] Sell order failed",
                    body=f"Decision was SELL but order failed at {datetime.now()}\n\n{exc}",
                )
    else:
        log.info("Decision NO BUY, no open position — nothing to do.")


def main():
    setup_logging()
    log.info(
        "Crude Oil Futures Trader started | symbol=%s | interval=%ds",
        FUTURES_SYMBOL, POLL_INTERVAL_SECONDS,
    )
    while True:
        run_cycle()
        log.info("Sleeping %ds until next cycle...\n", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
