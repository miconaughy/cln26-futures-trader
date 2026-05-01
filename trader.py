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

All user-configurable values are in the OPEN VARIABLES section below.
"""

import os
import re
import smtplib
import logging
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from openai import OpenAI
import schwab
import schwab.orders.common as orders

# ============================================================
# OPEN VARIABLES — Edit these to configure the script
# ============================================================

# --- xAI / Grok ---
GROK_API_KEY = os.environ.get("GROK_API_KEY", "your-xai-api-key-here")
GROK_MODEL   = "grok-3"

PROMPT = (
    'You are a seasoned futures trader that specializes within the commodity market - '
    'specifically crude oil futures. Using all of the most recent articles and publicly '
    'available information from the web, as well as market trends, make a determination '
    'to buy or not buy crude oil futures under the ticker symbol "/CLN26". '
    'Provide that determination in word format, but also a "1" for yes and a "0" for no.'
)

# --- Schwab / ThinkOrSwim ---
SCHWAB_APP_KEY      = os.environ.get("SCHWAB_APP_KEY",      "your-schwab-app-key")
SCHWAB_APP_SECRET   = os.environ.get("SCHWAB_APP_SECRET",   "your-schwab-app-secret")
SCHWAB_TOKEN_PATH   = "schwab_token.json"   # written on first OAuth login; refreshed automatically
SCHWAB_ACCOUNT_HASH = os.environ.get("SCHWAB_ACCOUNT_HASH", "your-account-hash")

FUTURES_SYMBOL   = "/CLN26"
ORDER_QUANTITY   = 1        # contracts per cycle

# --- Email alerts ---
ALERT_EMAIL_FROM     = "your-sender@gmail.com"
ALERT_EMAIL_TO       = "your-alert-email@example.com"
ALERT_EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "your-gmail-app-password")
SMTP_SERVER          = "smtp.gmail.com"
SMTP_PORT            = 587

# --- Timing ---
POLL_INTERVAL_SECONDS = 300   # 5 minutes

# ============================================================
# INTERNALS — No need to edit below this line
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.FileHandler("trader.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ---------- Grok ----------

def query_grok() -> tuple[int, str]:
    """Return (decision, full_response_text). Decision is 1 (buy) or 0 (no buy)."""
    client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=GROK_MODEL,
        messages=[{"role": "user", "content": PROMPT}],
    )
    text = response.choices[0].message.content.strip()
    # Take the last standalone 0 or 1 in the response as the signal
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
    """Place a market buy order for FUTURES_SYMBOL."""
    client = get_schwab_client()
    order = (
        orders.OrderBuilder()
        .set_order_type(orders.OrderType.MARKET)
        .set_session(orders.Session.NORMAL)
        .set_duration(orders.Duration.DAY)
        .set_order_strategy_type(orders.OrderStrategyType.SINGLE)
        .add_equity_leg(orders.EquityInstruction.BUY, FUTURES_SYMBOL, ORDER_QUANTITY)
    )
    resp = client.place_order(SCHWAB_ACCOUNT_HASH, order)
    resp.raise_for_status()
    log.info("Buy order placed: %s x%d", FUTURES_SYMBOL, ORDER_QUANTITY)


def execute_sell(quantity: int) -> None:
    """Place a market sell order to close the open FUTURES_SYMBOL position."""
    client = get_schwab_client()
    order = (
        orders.OrderBuilder()
        .set_order_type(orders.OrderType.MARKET)
        .set_session(orders.Session.NORMAL)
        .set_duration(orders.Duration.DAY)
        .set_order_strategy_type(orders.OrderStrategyType.SINGLE)
        .add_equity_leg(orders.EquityInstruction.SELL, FUTURES_SYMBOL, quantity)
    )
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

    # 3. Act based on decision × position state
    if decision == 1 and open_contracts == 0:
        log.info("Decision BUY, no open position — placing buy order.")
        try:
            execute_buy()
        except Exception as exc:
            log.error("Buy order failed: %s", exc)
            send_alert(
                subject="[Trader] Buy order failed",
                body=f"Decision was BUY but order failed at {datetime.now()}\n\n{exc}",
            )
    elif decision == 1 and open_contracts > 0:
        log.info("Decision BUY, already holding %d contract(s) — holding.", open_contracts)
    elif decision == 0 and open_contracts > 0:
        log.info("Decision NO BUY, closing open position of %d contract(s).", open_contracts)
        try:
            execute_sell(open_contracts)
        except Exception as exc:
            log.error("Sell order failed: %s", exc)
            send_alert(
                subject="[Trader] Sell order failed",
                body=f"Decision was SELL but order failed at {datetime.now()}\n\n{exc}",
            )
    else:
        log.info("Decision NO BUY, no open position — nothing to do.")


def main():
    log.info(
        "Crude Oil Futures Trader started | symbol=%s | interval=%ds",
        FUTURES_SYMBOL, POLL_INTERVAL_SECONDS,
    )

    # One-time check: warn if placeholders are still set
    for name, val in [
        ("GROK_API_KEY", GROK_API_KEY),
        ("SCHWAB_APP_KEY", SCHWAB_APP_KEY),
        ("SCHWAB_ACCOUNT_HASH", SCHWAB_ACCOUNT_HASH),
    ]:
        if val.startswith("your-"):
            log.warning("OPEN VARIABLE not set: %s — edit trader.py or set the env var.", name)

    while True:
        run_cycle()
        log.info("Sleeping %ds until next cycle...\n", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
