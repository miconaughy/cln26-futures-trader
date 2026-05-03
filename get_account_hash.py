#!/usr/bin/env python3
"""
After running first_login.py, run this to find your SCHWAB_ACCOUNT_HASH.
Copy the hashValue for your futures-enabled account into .env.
"""
from dotenv import load_dotenv
import os, json, schwab

load_dotenv()

client = schwab.auth.client_from_token_file(
    "schwab_token.json",
    os.environ["SCHWAB_APP_KEY"],
    os.environ["SCHWAB_APP_SECRET"],
)
print(json.dumps(client.get_account_numbers().json(), indent=2))
