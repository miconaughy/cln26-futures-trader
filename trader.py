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
    'You are a seasoned futures trader specializing in crude oil commodity markets. '
    'Your task is to issue a trading signal for crude oil futures under the ticker "/CLN26". '
    '\n\n'
    'Base your analysis exclusively on information and context from the past 12 months. '
    'Apply a strict recency bias when weighing evidence — information must be weighted '
    'in the following order, from most to least influential:\n'
    '  1. Today\'s news, price action, and market data (highest weight)\n'
    '  2. This week\'s developments\n'
    '  3. This month\'s trends\n'
    '  4. This quarter\'s macro and geopolitical context\n'
    '  5. The past 12 months of broader market history (lowest weight)\n'
    'Information older than 12 months must be ignored entirely unless it directly '
    'explains a current structural condition (e.g. a multi-year supply agreement still in force). '
    '\n\n'
    'Factors to analyze (weighted by recency):\n'
    '- Geopolitical events affecting oil supply or demand (OPEC+ decisions, sanctions, conflicts)\n'
    '- US and global crude inventory levels and EIA reports\n'
    '- Macroeconomic signals: USD strength, inflation, recession risk, demand outlook\n'
    '- Recent crude oil price trend and momentum\n'
    '- Any breaking news published today that could move oil prices\n'
    '\n\n'
    'Provide your analysis in plain language, then end your response with exactly one of '
    'these signals on its own line:\n'
    '  1  — Buy: conditions support opening a new long position\n'
    ' -1  — Sell: conditions support closing an existing long position\n'
    '  0  — Hold: no action warranted\n'
    'Output only the digit (1, -1, or 0) on the final line, with no other text on that line.'
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
ORDER_QUANTITY       = 1          # contracts per buy order
MAX_CONTRACTS        = 1          # maximum open contracts allowed at any time

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
_pnl_sub     = None   # account-level PnL subscription, set on connect
_pending_buy = False  # True after a buy order until IB confirms the position

# Shared state — read by ui.py for display
position_cache: dict = {
    "contracts":      0,
    "unrealized_pnl": None,
    "daily_pnl":      None,
    "updated_at":     None,
}
last_decision: dict = {"value": None, "updated_at": None}


_MONTH_CODES = {
    'F': '01', 'G': '02', 'H': '03', 'J': '04',
    'K': '05', 'M': '06', 'N': '07', 'Q': '08',
    'U': '09', 'V': '10', 'X': '11', 'Z': '12',
}


def parse_futures_symbol(symbol: str) -> "tuple[str, str, str] | None":
    """
    Parse a CME-style futures symbol like /CLN26.
    Returns (futures_symbol, ib_symbol, expiry_yyyymm) or None if invalid.
    Month codes: F=Jan G=Feb H=Mar J=Apr K=May M=Jun N=Jul Q=Aug U=Sep V=Oct X=Nov Z=Dec
    """
    m = re.match(r"^/([A-Z]{1,3})([FGHJKMNQUVXZ])(\d{2})$", symbol.strip().upper())
    if not m:
        return None
    ib_symbol, month_code, year_2 = m.groups()
    expiry = f"20{year_2}{_MONTH_CODES[month_code]}"
    return symbol.strip().upper(), ib_symbol, expiry


def get_ib() -> IB:
    """Return a connected IB instance, reconnecting automatically if the session dropped."""
    global _pnl_sub
    if not _ib.isConnected():
        import asyncio
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())
        _ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
        log.info("Connected to IB Gateway at %s:%d", IB_HOST, IB_PORT)
        accounts = _ib.managedAccounts()
        if accounts:
            _pnl_sub = _ib.reqPnL(accounts[0])
    return _ib


def get_contract() -> Future:
    return Future(
        symbol=IB_CONTRACT_SYMBOL,
        lastTradeDateOrContractMonth=IB_CONTRACT_EXPIRY,
        exchange=IB_CONTRACT_EXCHANGE,
        currency=IB_CONTRACT_CURRENCY,
    )


# ---------- Market data ----------

def _valid_price(value) -> bool:
    """Return True if value is a usable number (not None or NaN)."""
    import math
    try:
        return value is not None and not math.isnan(float(value))
    except (TypeError, ValueError):
        return False


def get_market_snapshot() -> str:
    """
    Fetch the current quote and recent 1-minute bars for the configured contract.
    Uses delayed data mode (type 3) so paper accounts without a live subscription
    still receive 15-min delayed quotes. Tries TRADES bars, falls back to MIDPOINT.
    Returns a formatted string ready to inject into the Grok prompt.
    """
    import math

    def _valid(v):
        try:
            return v is not None and not math.isnan(float(v))
        except (TypeError, ValueError):
            return False

    ib = get_ib()
    contract = get_contract()
    ib.qualifyContracts(contract)

    # Type 3 = delayed data, free for all IBKR accounts — must be set before reqMktData
    ib.reqMarketDataType(3)

    # Streaming subscription (not snapshot) — more reliable at receiving delayed ticks
    ticker = ib.reqMktData(contract, "", False, False)
    ib.sleep(4)
    ib.cancelMktData(contract)

    # Recent 1-minute bars — TRADES first, then MIDPOINT
    # Use 1800 S (30 min) — IB requires at least this duration for 1-min bars
    bars = []
    for show in ("TRADES", "MIDPOINT"):
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr="1800 S",
                barSizeSetting="1 min",
                whatToShow=show,
                useRTH=False,
                formatDate=1,
                keepUpToDate=False,
            )
        except Exception as exc:
            log.warning("reqHistoricalData whatToShow=%s failed: %s", show, exc)
        if bars:
            log.info("Market snapshot: %d bar(s) via whatToShow=%s", len(bars), show)
            break

    lines = [f"Live {IB_CONTRACT_SYMBOL} futures price (pulled from IBKR seconds ago):"]

    if _valid(ticker.last):
        lines.append(f"  Last traded price:  ${ticker.last:.2f}")
    if _valid(ticker.bid) and _valid(ticker.ask):
        lines.append(f"  Bid / Ask:          ${ticker.bid:.2f} / ${ticker.ask:.2f}")
    if _valid(ticker.close):
        lines.append(f"  Previous close:     ${ticker.close:.2f}")

    if bars:
        lines.append("  Recent 1-min bars (last 5, oldest → newest):")
        for bar in bars[-5:]:
            lines.append(
                f"    {bar.date}  O={bar.open:.2f}  H={bar.high:.2f}"
                f"  L={bar.low:.2f}  C={bar.close:.2f}  Vol={bar.volume}"
            )
        lines.append(f"  Current price (most recent bar close): ${bars[-1].close:.2f}")

    if len(lines) == 1:
        log.warning(
            "Market snapshot: no price data returned — market may be closed "
            "or the account lacks a market data subscription for this contract."
        )
        return (
            "No price data available from IBKR at this time (market closed or "
            "subscription required). Use your best estimate of the current crude "
            "oil price based on the most recent news and analysis available."
        )

    snapshot = "\n".join(lines)
    log.info("Market snapshot injected into Grok prompt:\n%s", snapshot)
    return snapshot


# ---------- Grok ----------

def query_grok() -> tuple[int, str]:
    """Return (decision, full_response_text). Decision is 1 (buy), -1 (sell), or 0 (hold)."""
    now = datetime.now()

    try:
        price_context = get_market_snapshot()
    except Exception as exc:
        log.warning("Could not fetch market snapshot for Grok: %s", exc)
        price_context = "Current price data unavailable from IBKR."

    date_context = (
        f'\n\nToday is {now.strftime("%A, %B %d, %Y")}. '
        f'The 12-month lookback window is {now.strftime("%B %d, %Y")} back to '
        f'{now.replace(year=now.year - 1).strftime("%B %d, %Y")}. '
        f'Prioritize any information published or updated today above all else. '
        f'Discard any data, articles, or analysis dated before '
        f'{now.replace(year=now.year - 1).strftime("%B %Y")}.'
    )

    live_price_context = (
        f"\n\nCURRENT MARKET DATA (treat this as ground truth for the current price):\n"
        f"{price_context}\n"
    )

    log.info("Running prompt in Grok...")
    client = OpenAI(api_key=GROK_API_KEY, base_url="https://api.x.ai/v1")
    response = client.chat.completions.create(
        model=GROK_MODEL,
        messages=[{"role": "user", "content": PROMPT + date_context + live_price_context}],
    )
    text = response.choices[0].message.content.strip()
    signals = re.findall(r"(?<!\d)(-1|0|1)(?!\d)", text)
    decision = int(signals[-1]) if signals else 0
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
    """Return long contract count and update position_cache with P&L data."""
    global _pending_buy
    ib = get_ib()

    # Force IB Gateway to send fresh position data before reading
    ib.reqPositions()
    ib.sleep(2)

    # Match on symbol + secType only — IB returns the last-trade date (e.g. '20260622'),
    # not the delivery month ('202607'), so startswith matching on expiry is unreliable.
    count = 0
    for pos in ib.positions():
        c = pos.contract
        if c.symbol == IB_CONTRACT_SYMBOL and c.secType == 'FUT':
            count = int(pos.position) if pos.position > 0 else 0
            break

    # Once IB confirms the position, clear the pending-buy guard
    if count > 0:
        _pending_buy = False

    # P&L from portfolio() — separate from position count
    unrealized_pnl = None
    for item in ib.portfolio():
        c = item.contract
        if c.symbol == IB_CONTRACT_SYMBOL and c.secType == 'FUT':
            unrealized_pnl = item.unrealizedPNL
            break

    daily_pnl = _pnl_sub.dailyPnL if _pnl_sub is not None else None

    position_cache["contracts"]      = count
    position_cache["unrealized_pnl"] = unrealized_pnl
    position_cache["daily_pnl"]      = daily_pnl
    position_cache["updated_at"]     = datetime.now()
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


def close_all_positions() -> None:
    """Immediately close all open contracts. Safe to call at any time."""
    global _pending_buy
    ib = get_ib()
    ib.reqPositions()
    ib.sleep(2)

    quantity = 0
    for pos in ib.positions():
        c = pos.contract
        if c.symbol == IB_CONTRACT_SYMBOL and c.secType == 'FUT':
            quantity = int(pos.position) if pos.position > 0 else 0
            break

    if quantity == 0:
        log.info("Exit All: no open contracts to close.")
        return

    log.info("Exit All: closing %d contract(s) immediately.", quantity)
    execute_sell(quantity)
    _pending_buy = False


# ---------- Main cycle ----------

def run_cycle() -> None:
    global _pending_buy
    log.info("=== Cycle start ===")
    decision = 0

    # Fetch position and P&L first so the UI reflects current state before Grok runs
    try:
        open_contracts = get_open_position()
        log.info("Open position: %d contract(s) | Unrealized P&L: %s | Daily P&L: %s",
                 open_contracts,
                 f"${position_cache['unrealized_pnl']:,.2f}" if position_cache["unrealized_pnl"] is not None else "—",
                 f"${position_cache['daily_pnl']:,.2f}"      if position_cache["daily_pnl"]      is not None else "—")
    except Exception as exc:
        log.error("Failed to fetch position: %s", exc)
        send_alert(
            subject="[Trader] Position check failed — skipping cycle",
            body=f"Could not read account positions at {datetime.now()}\n\n{exc}",
        )
        return

    try:
        decision, response_text = query_grok()
        label = {1: "BUY", -1: "SELL", 0: "HOLD"}.get(decision, str(decision))
        log.info("Grok says:\n%s", response_text)
        log.info("Parsed signal: %s (%d)", label, decision)
        last_decision["value"]      = decision
        last_decision["updated_at"] = datetime.now()
    except Exception as exc:
        log.error("Grok query failed: %s", exc)
        send_alert(
            subject="[Trader] Grok API error — defaulting to HOLD",
            body=f"Error at {datetime.now()}\n\n{exc}",
        )

    # Treat a pending (unconfirmed) buy as already at the limit
    effective_contracts = MAX_CONTRACTS if _pending_buy else open_contracts

    if decision == 1 and effective_contracts < MAX_CONTRACTS:
        log.info("Signal BUY, holding %d/%d contract(s) — placing buy order.",
                 open_contracts, MAX_CONTRACTS)
        try:
            execute_buy()
            _pending_buy = True
        except Exception as exc:
            log.error("Buy order failed: %s", exc)
            send_alert(
                subject="[Trader] Buy order failed",
                body=f"Signal was BUY but order failed at {datetime.now()}\n\n{exc}",
            )
    elif decision == 1 and effective_contracts >= MAX_CONTRACTS:
        log.info("Signal BUY, already at max %d contract(s) — holding.", MAX_CONTRACTS)
    elif decision == -1 and open_contracts > 0:
        log.info("Signal SELL, closing open position of %d contract(s).", open_contracts)
        try:
            execute_sell(open_contracts)
            _pending_buy = False
        except Exception as exc:
            log.error("Sell order failed: %s", exc)
            send_alert(
                subject="[Trader] Sell order failed",
                body=f"Signal was SELL but order failed at {datetime.now()}\n\n{exc}",
            )
    elif decision == -1 and open_contracts == 0:
        log.info("Signal SELL, no open position — nothing to close.")
    else:
        log.info("Signal HOLD — no action.")


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
