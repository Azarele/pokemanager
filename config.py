import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
EXCEL_FILE: str = os.getenv("EXCEL_FILE", "inventory.xlsx")
COMMAND_PREFIX: str = os.getenv("COMMAND_PREFIX", "!")

# --- Lister paths --------------------------------------------------------
BROWSER_STATE_DIR: str = os.getenv("BROWSER_STATE_DIR", "browser_state")
EBAY_STATE_PATH:   str = os.path.join(BROWSER_STATE_DIR, "ebay_state.json")
VINTED_STATE_PATH: str = os.path.join(BROWSER_STATE_DIR, "vinted_state.json")
TEMP_IMAGES_DIR:   str = os.getenv("TEMP_IMAGES_DIR", "temp_images")

# Run the Vinted listing browser visibly instead of headless. Headed Chromium
# is harder for Datadome to flag as automation. Requires a display (WSLg, a
# real desktop, or Xvfb) — set to true for a true headless server deployment.
VINTED_HEADLESS: bool = os.getenv("VINTED_HEADLESS", "false").lower() == "true"

# --- Background price updater -------------------------------------------
# Channel ID where the 12-hour update summary embed is posted.
PRICE_UPDATE_CHANNEL_ID: int = int(os.getenv("PRICE_UPDATE_CHANNEL_ID", "0"))
# How often to run the background price refresh (in hours).
UPDATE_INTERVAL_HOURS: float = float(os.getenv("UPDATE_INTERVAL_HOURS", "12"))

# --- AI listing assistant ------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Guild ID for instant slash-command sync during development.
# Set to 0 (or omit from .env) to use global sync instead — works on any
# server but takes up to 1 hour to propagate after bot restart.
TEST_GUILD_ID: int = int(os.getenv("TEST_GUILD_ID", "0"))

# Multiplier applied to Japanese PriceCharting prices when the card is actually a Korean print.
# Korean prints trade at a discount to their Japanese equivalents on the secondary market.
KOREAN_PRICE_MULTIPLIER: float = float(os.getenv("KOREAN_PRICE_MULTIPLIER", "0.7"))

# ── Backups ───────────────────────────────────────────────────────────────────
BACKUP_DIR: str = os.getenv("BACKUP_DIR", "backups")
OWNER_USER_ID: int = int(os.getenv("OWNER_USER_ID", "0"))
BACKUP_RETENTION_COUNT: int = int(os.getenv("BACKUP_RETENTION_COUNT", "50"))

# ── Audit log ─────────────────────────────────────────────────────────────────
AUDIT_LOG_DIR: str = os.getenv("AUDIT_LOG_DIR", "logs")

# --- eBay Developer API --------------------------------------------------
EBAY_APP_ID:   str = os.getenv("EBAY_APP_ID", "")
EBAY_DEV_ID:   str = os.getenv("EBAY_DEV_ID", "")
EBAY_CERT_ID:  str = os.getenv("EBAY_CERT_ID", "")
EBAY_REFRESH_TOKEN: str = os.getenv("EBAY_REFRESH_TOKEN", "")

# Business policies — required for Inventory API offers.
# Find these at: ebay.co.uk → Account → Business policies
# Or via: python -c "import asyncio, lister_ebay_api; asyncio.run(lister_ebay_api.print_policies())"
EBAY_FULFILLMENT_POLICY_ID: str = os.getenv("EBAY_FULFILLMENT_POLICY_ID", "")
EBAY_PAYMENT_POLICY_ID:     str = os.getenv("EBAY_PAYMENT_POLICY_ID", "")
EBAY_RETURN_POLICY_ID:      str = os.getenv("EBAY_RETURN_POLICY_ID", "")

# Pokémon Trading Cards category ID on eBay UK. Override if 183454 is rejected.
EBAY_CATEGORY_ID: str = os.getenv("EBAY_CATEGORY_ID", "183454")

EBAY_MIN_PRICE_GBP: float = float(os.getenv("EBAY_MIN_PRICE_GBP", "0.99"))
EBAY_FEE_RATE: float = float(os.getenv("EBAY_FEE_RATE", "0.1235"))  # 12.35% eBay UK standard rate

# Estimated minutes of work per card (photography + listing + packing + postage).
# Used by /efficiency to calculate profit-per-hour.
EFFORT_MINUTES_PER_CARD: int = int(os.getenv("EFFORT_MINUTES_PER_CARD", "15"))
HOURLY_RATE_GBP: float = float(os.getenv("HOURLY_RATE_GBP", "12.0"))  # UK minimum wage

# Set to False to refresh inventory prices without pushing changes to live eBay listings.
AUTO_SYNC_EBAY_PRICES: bool = os.getenv("AUTO_SYNC_EBAY_PRICES", "true").lower() == "true"

# Redirect URI registered in the eBay developer portal (User Tokens → Auth Accepted URLs).
# Must match exactly when exchanging the auth code in generate_ebay_token.py.
EBAY_REDIRECT_URI: str = os.getenv(
    "EBAY_REDIRECT_URI",
    "https://auth.ebay.com/oauth2/ThirdPartyAuthSucessFailure?isAuthSuccessful=true",
)

if not BOT_TOKEN:
    raise EnvironmentError("DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in.")

if not all([EBAY_APP_ID, EBAY_CERT_ID, EBAY_REFRESH_TOKEN]):
    print("[config] Warning: eBay API credentials incomplete — /list will fail. Run generate_ebay_token.py.")
