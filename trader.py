#!/usr/bin/env python3
"""
Crude Oil Futures Automated Trader — IBKR Edition

Every POLL_INTERVAL_SECONDS the script:
  1. Sends PROMPT to Grok and parses a buy decision (1 = buy, 0 = no buy).
  2. Checks the account for an open position in the configured futures contract.
  3. decision=1, no position  → place a market buy order.
     decision=1, position open → hold (no duplicate buy).
     decision=0, position open → close the position with a market sell.
     decision=0, no position  → do nothing.
  4. On any error → defaults to 0, waits for the next cycle, and sends an email alert.

Prerequisites:
  - IB Gateway (or TWS) must be running with API access enabled.
  - Paper trading port: 7497. Live trading port: 7496.
  - Enable API: IB Gateway → Configure → API → Settings → Enable ActiveX and Socket Clients
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
from ib_insync import IB, Future, MarketOrder

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

# --- IBKR / IB Gateway ---
IB_HOST      = "127.0.0.1"
IB_PORT      = 4002   # 4002 = IB Gateway paper trading, 4001 = IB Gateway live trading
IB_CLIENT_ID = 1

# --- Futures contract ---
# /CLN26 = Crude Oil (CL), July 2026 (N = July), NYMEX
FUTURES_SYMBOL       = "/CLN26"
IB_CONTRACT_SYMBOL   = "CL"
IB_CONTRACT_EXPIRY   = "202607"   # YYYYMM
IB_CONTRACT_EXCHANGE = "NYMEX"
IB_CONTRACT_CURRENCY = "USD"
ORDER_QUANTITY       = 1          # contracts per cycle

# --- Email alerts ---
ALERT_EMAIL_FROM     = os.environ.get("ALERT_EMAIL_FROM", "your-sender@gmail.com")
ALERT_EMAIL_TO       = os.environ.get("ALERT_EMAIL_TO",   "your-alert-email@example.com")
ALERT_EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD",   "your-gmail-app-password")
SMTP_SERVER          = "smtp.gmail.com"
SMTP_PORT            = 587

# --- Timing ---
POLL_INTERVAL_SECONDS = 300   # 5 minutes

# ============================================================
# INTERNALS — No need to edit below this line
# ============================================================

log = logging.getLogger(__name__)

_ib = IB()

# Shared state — read by ui.py for display
position_cache: dict = {"contracts": 0, "updated_at": None}
last_decision:  dict = {"value": None, "updated_at": None}


def get_ib() -> IB:
    """Return a connected IB instance, reconnecting automatically if the session dropped."""
    if not _ib.isConnected():
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        _ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
        log.info("Connected to IB Gateway at %s:%d", IB_HOST, IB_PORT)
    return _ib


def get_contract() -> Future:
    return Future(
        symbol=IB_CONTRACT_SYMBOL,
        lastTradeDateOrContractMonth=IB_CONTRACT_EXPIRY,
        exchange=IB_CONTRACT_EXCHANGE,
        currency=IB_CONTRACT_CURRENCY,
    )


# ---------- Grok ----------

def query_grok() -> tuple[int, str]:
    """Return (decision, full_response_text). Decision is 1 (buy) or 0 (no buy)."""
    client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=GROK_MODEL,
        messages=[{"role": "user", "content": PROMPT}],
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


# ---------- IBKR ----------

def get_open_position() -> int:
    """Return the number of long contracts held for the configured futures contract, or 0."""
    ib = get_ib()
    count = 0
    for pos in ib.positions():
        c = pos.contract
        if (c.symbol == IB_CONTRACT_SYMBOL
                and c.lastTradeDateOrContractMonth.startswith(IB_CONTRACT_EXPIRY)):
            count = int(pos.position) if pos.position > 0 else 0
            break
    position_cache["contracts"]  = count
    position_cache["updated_at"] = datetime.now()
    return count


def execute_buy() -> None:
    """Place a market buy order for ORDER_QUANTITY contracts."""
    ib = get_ib()
    contract = get_contract()
    ib.qualifyContracts(contract)
    trade = ib.placeOrder(contract, MarketOrder("BUY", ORDER_QUANTITY))
    ib.sleep(1)
    log.info("Buy order placed: %s x%d — status: %s",
             FUTURES_SYMBOL, ORDER_QUANTITY, trade.orderStatus.status)


def execute_sell(quantity: int) -> None:
    """Place a market sell order to close the open position."""
    ib = get_ib()
    contract = get_contract()
    ib.qualifyContracts(contract)
    trade = ib.placeOrder(contract, MarketOrder("SELL", quantity))
    ib.sleep(1)
    log.info("Sell order placed: %s x%d — status: %s",
             FUTURES_SYMBOL, quantity, trade.orderStatus.status)


# ---------- Main cycle ----------

def run_cycle() -> None:
    log.info("=== Cycle start ===")
    decision = 0

    try:
        decision, response_text = query_grok()
        log.info("Grok says:\n%s", response_text)
        log.info("Parsed decision: %d", decision)
        last_decision["value"]      = decision
        last_decision["updated_at"] = datetime.now()
    except Exception as exc:
        log.error("Grok query failed: %s", exc)
        send_alert(
            subject="[Trader] Grok API error — defaulting to NO BUY",
            body=f"Error at {datetime.now()}\n\n{exc}",
        )

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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        handlers=[
            logging.FileHandler("trader.log"),
            logging.StreamHandler(),
        ],
    )
    log.info(
        "Crude Oil Futures Trader started | symbol=%s | port=%d | interval=%ds",
        FUTURES_SYMBOL, IB_PORT, POLL_INTERVAL_SECONDS,
    )

    if GROK_API_KEY.startswith("your-"):
        log.warning("GROK_API_KEY not set — edit trader.py or set the env var.")

    get_ib()  # connect at startup; auto-reconnects on each cycle if needed

    while True:
        run_cycle()
        log.info("Sleeping %ds until next cycle...\n", POLL_INTERVAL_SECONDS)
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
