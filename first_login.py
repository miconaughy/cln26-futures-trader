#!/usr/bin/env python3
"""
Test the connection to IB Gateway and confirm the futures contract is valid.
Run this before starting trader.py to verify everything is wired up correctly.

Prerequisites:
  - IB Gateway (or TWS) must be running
  - API must be enabled: Configure → API → Settings → Enable ActiveX and Socket Clients
  - Paper trading port: 4002 | Live trading port: 4001
"""
from ib_insync import IB, Future
from trader import (
    IB_HOST, IB_PORT, IB_CLIENT_ID,
    IB_CONTRACT_SYMBOL, IB_CONTRACT_EXPIRY,
    IB_CONTRACT_EXCHANGE, IB_CONTRACT_CURRENCY,
    FUTURES_SYMBOL,
)

ib = IB()
print(f"Connecting to IB Gateway at {IB_HOST}:{IB_PORT}...")
ib.connect(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
print("Connected.")

contract = Future(
    symbol=IB_CONTRACT_SYMBOL,
    lastTradeDateOrContractMonth=IB_CONTRACT_EXPIRY,
    exchange=IB_CONTRACT_EXCHANGE,
    currency=IB_CONTRACT_CURRENCY,
)

print(f"\nQualifying contract {FUTURES_SYMBOL}...")
qualified = ib.qualifyContracts(contract)
if qualified:
    c = qualified[0]
    print(f"  Contract ID : {c.conId}")
    print(f"  Symbol      : {c.symbol}")
    print(f"  Expiry      : {c.lastTradeDateOrContractMonth}")
    print(f"  Exchange    : {c.exchange}")
    print(f"  Currency    : {c.currency}")
else:
    print("  WARNING: Contract could not be qualified. Check the expiry date in trader.py.")

print("\nOpen positions:")
positions = ib.positions()
if positions:
    for pos in positions:
        print(f"  {pos.contract.symbol} {pos.contract.lastTradeDateOrContractMonth}: {pos.position} contracts")
else:
    print("  No open positions.")

ib.disconnect()
print("\nConnection test complete. You can now run: python3 trader.py")
