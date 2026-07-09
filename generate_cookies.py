"""
generate_cookies.py — one-time interactive login helper (CDP edition).

Instead of launching a Playwright-controlled browser (which Akamai detects),
this script connects to your *existing* everyday Chrome via the Chrome
DevTools Protocol.  Because Chrome is a real, unmodified browser process,
no WAF fingerprinting applies.

Prerequisites
-------------
You MUST start Chrome with remote debugging enabled BEFORE running this script.
See the error message printed if the connection fails for exact commands.

Usage
-----
    python generate_cookies.py ebay       # eBay UK only
    python generate_cookies.py vinted     # Vinted UK only
    python generate_cookies.py all        # both (sequential)

Output
------
    browser_state/ebay_state.json
    browser_state/vinted_state.json
"""

import asyncio
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_CDP_URL = "http://localhost:9222"

PLATFORMS = {
    "ebay": {
        "login_url":  "https://signin.ebay.co.uk/signin/",
        "state_file": "browser_state/ebay_state.json",
        "detect_login": lambda url: (
            "signin.ebay.co.uk" not in url
            and "ebay.co.uk" in url
        ),
        "label": "eBay UK",
    },
    "vinted": {
        "login_url":  "https://www.vinted.co.uk/login",
        "state_file": "browser_state/vinted_state.json",
        "detect_login": lambda url: (
            "/login" not in url
            and "vinted.co.uk" in url
        ),
        "label": "Vinted UK",
    },
}


# ---------------------------------------------------------------------------
# Friendly error helper
# ---------------------------------------------------------------------------

def _print_cdp_help(exc: Exception) -> None:
    """Print a clear, step-by-step explanation of how to fix a CDP connection failure."""
    bar = "=" * 62
    print(f"\n{bar}")
    print("  CONNECTION FAILED — Chrome is not listening on port 9222")
    print(bar)
    print(f"\n  Error detail: {exc}\n")
    print("  You must close all existing Chrome windows, then relaunch")
    print("  Chrome WITH the remote-debugging flag before running this")
    print("  script.  Choose the command for your OS:\n")

    print("  ── Windows (run in PowerShell or the Run dialog Win+R) ──")
    print('  & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe"')
    print("      --remote-debugging-port=9222 --profile-directory=Default\n")

    print("  ── macOS ────────────────────────────────────────────────")
    print('  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\')
    print("      --remote-debugging-port=9222 --profile-directory=Default\n")

    print("  ── Linux ────────────────────────────────────────────────")
    print("  google-chrome --remote-debugging-port=9222 --profile-directory=Default\n")

    print("  ── WSL 2 note ───────────────────────────────────────────")
    print("  Run the Windows command above in a Windows terminal (not WSL).")
    print("  localhost:9222 is automatically forwarded into WSL 2 on")
    print("  Windows 11 / WSL 0.67+.  If the connection still fails,")
    print(f"  replace 'localhost' in _CDP_URL with your Windows host IP")
    print("  (find it with: ip route show | grep default | awk '{print $3}').\n")

    print("  After Chrome opens, run this script again.")
    print(f"{bar}\n")


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

async def save_state_for(platform_key: str, browser) -> None:
    """
    Open a new tab in the connected Chrome, wait for the user to log in,
    save the full browser-context state, then close the tab.

    Parameters
    ----------
    platform_key : "ebay" or "vinted"
    browser      : Playwright Browser object from connect_over_cdp()
    """
    cfg        = PLATFORMS[platform_key]
    state_path = Path(cfg["state_file"])
    state_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  Platform : {cfg['label']}")
    print(f"  State    : {state_path}")
    print(f"{'='*60}")
    print("  A new tab will open in your existing Chrome window.")
    print("  Log in normally, then come back here and press Enter.")
    print()

    # browser.contexts[0] is the user's real Chrome profile — it already
    # carries their full cookie jar, extensions, and any prior sessions.
    context = browser.contexts[0] if browser.contexts else await browser.new_context()

    page = await context.new_page()

    try:
        await page.goto(cfg["login_url"], wait_until="domcontentloaded", timeout=30_000)
    except Exception as exc:
        print(f"  ⚠  Could not navigate to {cfg['login_url']}: {exc}")
        print("     Navigate there manually in the new tab, then press Enter.")

    # Poll the tab every 5 s and print a running status hint
    poll_task = asyncio.create_task(_poll_login_state(page, cfg))

    await asyncio.to_thread(input, "  → Press Enter when you are fully logged in: ")
    poll_task.cancel()

    current_url = page.url
    logged_in   = cfg["detect_login"](current_url)

    if not logged_in:
        print(f"\n  ⚠  Warning: current URL still looks like a login page.")
        print(f"     ({current_url[:80]})")
        print("     The saved state may not contain a valid session.")

    # storage_state() captures cookies + localStorage for the whole context
    # via CDP — this works on the real Chrome profile context.
    await context.storage_state(path=str(state_path))

    # Close only the tab we opened; leave the rest of Chrome untouched
    await page.close()

    if logged_in:
        print(f"\n  ✅  State saved → {state_path}")
    else:
        print(f"\n  ⚠  State saved with warnings → {state_path}")
        print("     If the bot reports 'session expired', re-run this script.")


async def _poll_login_state(page, cfg: dict) -> None:
    """Print a live URL hint every 5 seconds until cancelled."""
    try:
        while True:
            await asyncio.sleep(5)
            url = page.url
            if cfg["detect_login"](url):
                print("  ✅ Login detected — press Enter to save the session.")
            else:
                print(f"  ⏳ Waiting for login… (current: {url[:70]})")
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1].lower() not in ("ebay", "vinted", "all"):
        print(__doc__)
        sys.exit(1)

    target = sys.argv[1].lower()
    keys   = list(PLATFORMS.keys()) if target == "all" else [target]

    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        # --- Connect to the user's real Chrome via CDP ---
        print(f"\n  Connecting to Chrome at {_CDP_URL} …")
        try:
            browser = await pw.chromium.connect_over_cdp(_CDP_URL)
        except Exception as exc:
            _print_cdp_help(exc)
            sys.exit(1)

        ctx_count = len(browser.contexts)
        print(f"  Connected. ({ctx_count} browser context{'s' if ctx_count != 1 else ''} found)\n")

        for key in keys:
            await save_state_for(key, browser)

        # Do NOT call browser.close() — that is the user's real Chrome.
        # Exiting the async_playwright context manager simply drops the CDP
        # session; Chrome itself keeps running normally.

    print("\n  Done. You can now start the bot normally.\n")


if __name__ == "__main__":
    asyncio.run(main())
