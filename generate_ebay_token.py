"""
generate_ebay_token.py — one-time eBay OAuth consent helper.

Run once to get a refresh token for lister_ebay_api.py.
The refresh token is valid for 18 months and only needs to be re-obtained
if it expires or you revoke it in the eBay developer portal.

Prerequisites
-------------
1. Create a free account at developer.ebay.com and create a production app.
2. In your app's settings under "User Tokens → Auth Accepted URLs", leave the field
   blank (eBay's default success page) or confirm that the registered URI matches
   EBAY_REDIRECT_URI in .env.
3. Copy App ID, Dev ID, and Cert ID into .env as EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID.

Usage
-----
    python generate_ebay_token.py
"""

import base64
import os
import sys
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv

load_dotenv()

_AUTH_URL  = "https://auth.ebay.com/oauth2/authorize"
_TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"

# Read from .env so it stays in sync with config.EBAY_REDIRECT_URI.
# Default matches eBay's built-in success page (no custom redirect server needed).
# Must match exactly what is registered under "Auth Accepted URLs" in the developer portal.
_REDIRECT_URI = os.getenv(
    "EBAY_REDIRECT_URI",
    "https://auth.ebay.com/oauth2/ThirdPartyAuthSucessFailure?isAuthSuccessful=true",
)

_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
])


def main() -> None:
    app_id  = os.getenv("EBAY_APP_ID", "").strip()
    cert_id = os.getenv("EBAY_CERT_ID", "").strip()

    if not app_id or not cert_id:
        print(
            "ERROR: EBAY_APP_ID and EBAY_CERT_ID must be set in .env.\n"
            "Get them from developer.ebay.com → My Account → Application Keys."
        )
        sys.exit(1)

    consent_url = (
        f"{_AUTH_URL}?"
        + urlencode({
            "client_id":     app_id,
            "redirect_uri":  _REDIRECT_URI,
            "response_type": "code",
            "scope":         _SCOPES,
        })
    )

    print("=" * 70)
    print("NOTE: New scope added (sell.fulfillment.readonly).")
    print("If you have an existing refresh token, you must re-run this script")
    print("to get a new token with the updated scopes.")
    print("=" * 70)
    print("Step 1 — Open this URL in your browser and log in to eBay:")
    print()
    print(consent_url)
    print()
    print("Step 2 — After granting permission, you'll land on an eBay success page.")
    print("         Copy the full URL from your browser's address bar and paste it below.")
    print("=" * 70)
    print()

    redirect_raw = input("Paste the full redirect URL here: ").strip()

    try:
        parsed = urlparse(redirect_raw)
        qs     = parse_qs(parsed.query)
        code   = qs["code"][0]
    except (KeyError, IndexError):
        print(f"\nERROR: Could not find 'code' in the pasted URL:\n  {redirect_raw}")
        sys.exit(1)

    print(f"\n[token] Extracted auth code: {code[:20]}…")
    print("[token] Exchanging auth code for tokens…")

    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()

    resp = requests.post(
        _TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type":   "authorization_code",
            "code":         code,
            "redirect_uri": _REDIRECT_URI,
        },
        timeout=30,
    )

    if resp.status_code != 200:
        print(f"\nERROR: Token exchange failed (HTTP {resp.status_code}):\n{resp.text}")
        sys.exit(1)

    tokens        = resp.json()
    refresh_token = tokens.get("refresh_token", "")
    access_token  = tokens.get("access_token", "")
    expires_in    = tokens.get("refresh_token_expiry", "18 months")

    print()
    print("=" * 70)
    print("SUCCESS")
    print()
    print("Access token (expires ~2 hours — for debugging only, do not save):")
    print(f"  {access_token[:80]}…")
    print()
    print(f"Refresh token (valid for {expires_in} — ADD THIS TO .env):")
    print(f"  {refresh_token}")
    print()
    print("Add to .env:")
    print()
    print(f"  EBAY_REFRESH_TOKEN={refresh_token}")
    print()
    print("After that, find your business policy IDs by running:")
    print('  python -c "import asyncio, lister_ebay_api; asyncio.run(lister_ebay_api.print_policies())"')
    print("=" * 70)


if __name__ == "__main__":
    main()
