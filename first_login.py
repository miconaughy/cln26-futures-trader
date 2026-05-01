#!/usr/bin/env python3
"""
Run this once to authenticate with Schwab and save schwab_token.json.
After this, trader.py handles token refresh automatically.
"""
import schwab
from trader import SCHWAB_APP_KEY, SCHWAB_APP_SECRET, SCHWAB_TOKEN_PATH

schwab.auth.client_from_login_flow(
    SCHWAB_APP_KEY,
    SCHWAB_APP_SECRET,
    redirect_uri="https://127.0.0.1",
    token_path=SCHWAB_TOKEN_PATH,
)
print(f"\nSuccess — token saved to {SCHWAB_TOKEN_PATH}")
print("You can now run: python3 trader.py")
