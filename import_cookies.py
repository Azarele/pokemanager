"""
import_cookies.py — convert a Cookie-Editor JSON export into a Playwright
                    storage_state file that the bot can load directly.

Workflow
--------
1. In Chrome, log in to eBay UK or Vinted UK normally.
2. Open the Cookie-Editor extension → Export → Export as JSON.
3. Paste the copied JSON into a file called  raw_cookies.txt  in this folder.
4. Run:  python import_cookies.py
5. Delete raw_cookies.txt when prompted (it contains live session tokens).

Output
------
    browser_state/ebay_state.json
    browser_state/vinted_state.json
"""

import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RAW_FILE = Path("raw_cookies.txt")

PLATFORM_PATHS = {
    "ebay":   Path("browser_state/ebay_state.json"),
    "vinted": Path("browser_state/vinted_state.json"),
}

# Cookie-Editor sameSite labels → Playwright sameSite labels
_SAME_SITE_MAP = {
    "no_restriction": "None",
    "lax":            "Lax",
    "strict":         "Strict",
    "unspecified":    "None",
}


# ---------------------------------------------------------------------------
# Cookie normalisation
# ---------------------------------------------------------------------------

def _normalise(raw: dict) -> dict:
    """
    Convert one Cookie-Editor cookie dict to Playwright's storage_state format.

    Field differences handled:
      - "expirationDate" (float, Unix timestamp) → "expires"
      - "sameSite"       (lowercase/snake_case)  → Title-case Playwright expects
      - Extra fields (hostOnly, session, storeId) are dropped — Playwright
        does not use them and they can trigger schema validation warnings.
    """
    same_site_raw = str(raw.get("sameSite", "no_restriction")).lower()

    return {
        "name":     raw["name"],
        "value":    raw["value"],
        "domain":   raw["domain"],
        "path":     raw.get("path", "/"),
        "expires":  raw.get("expirationDate", raw.get("expires", -1)),
        "httpOnly": bool(raw.get("httpOnly", False)),
        "secure":   bool(raw.get("secure", False)),
        "sameSite": _SAME_SITE_MAP.get(same_site_raw, "None"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    # ── 1. Read raw_cookies.txt ──────────────────────────────────────────
    if not RAW_FILE.exists():
        print(f"\nError: '{RAW_FILE}' not found.")
        print("Export your cookies from the Cookie-Editor extension and save")
        print(f"the JSON into '{RAW_FILE}', then re-run this script.\n")
        sys.exit(1)

    raw_text = RAW_FILE.read_text(encoding="utf-8").strip()

    try:
        raw_cookies = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        print(f"\nError: '{RAW_FILE}' is not valid JSON.")
        print(f"Details: {exc}\n")
        sys.exit(1)

    if not isinstance(raw_cookies, list):
        print(f"\nError: expected a JSON array, got {type(raw_cookies).__name__}.")
        print("Re-export from Cookie-Editor using 'Export as JSON'.\n")
        sys.exit(1)

    if not raw_cookies:
        print(f"\nError: '{RAW_FILE}' contains an empty array.  Nothing to import.\n")
        sys.exit(1)

    # ── 2. Normalise field names ─────────────────────────────────────────
    try:
        cookies = [_normalise(c) for c in raw_cookies]
    except KeyError as exc:
        print(f"\nError: a cookie entry is missing required field {exc}.")
        print("Make sure you exported from Cookie-Editor in JSON format.\n")
        sys.exit(1)

    # ── 3. Ask which platform ────────────────────────────────────────────
    print()
    while True:
        platform = input("  Are these cookies for 'ebay' or 'vinted'? ").strip().lower()
        if platform in PLATFORM_PATHS:
            break
        print("  Please type exactly  ebay  or  vinted.")

    # ── 4. Write storage_state JSON ──────────────────────────────────────
    out_path = PLATFORM_PATHS[platform]
    out_path.parent.mkdir(parents=True, exist_ok=True)

    storage_state = {"cookies": cookies, "origins": []}
    out_path.write_text(
        json.dumps(storage_state, indent=2),
        encoding="utf-8",
    )

    # ── 5. Confirm and warn ──────────────────────────────────────────────
    print(f"\n  ✅  {len(cookies)} cookie(s) saved → {out_path}")
    print()
    print("  ⚠   SECURITY REMINDER")
    print(f"      Delete '{RAW_FILE}' now — it contains live session tokens")
    print("      that would give anyone who reads it full access to your account.")
    print(f"      Run:  rm {RAW_FILE}")
    print()


if __name__ == "__main__":
    main()
