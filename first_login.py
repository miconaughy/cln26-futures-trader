#!/usr/bin/env python3
"""
Run this once to authenticate with Schwab and save schwab_token.json.
After this, trader.py handles token refresh automatically.
"""
from dotenv import load_dotenv
import os
import schwab

load_dotenv()

schwab.auth.client_from_login_flow(
    os.environ["SCHWAB_APP_KEY"],
    os.environ["SCHWAB_APP_SECRET"],
    callback_url="https://127.0.0.1:8182",
    token_path="schwab_token.json",
)
print("\nSuccess — token saved to schwab_token.json")
print("You can now run: python3 trader.py")
