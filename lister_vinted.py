"""
lister_vinted.py — async Vinted UK listing automation via Playwright.

Public interface
----------------
    result = await list_item_on_vinted(
        item_name   = "Charizard Holo Base Set",
        price_gbp   = 45.00,
        image_paths = [Path("temp_images/item_5/0.jpg"), ...],
        condition   = "good",       # see CONDITION_MAP below
        description = "Near mint",  # optional
        brand       = "",           # optional
        dry_run     = False,
    )

    result.success      → bool
    result.listing_url  → str | None
    result.error        → str | None

Authentication
--------------
Loads saved browser state from config.VINTED_STATE_PATH.
Run generate_cookies.py vinted first to create that file.

Vinted listing flow (multi-step wizard)
----------------------------------------
  1. Navigate to /items/new
  2. Upload photos
  3. Fill title, description
  4. Select category (auto-suggestion or first match)
  5. Select condition
  6. Set price
  7. Submit

Selector stability
------------------
Vinted's form is more stable than eBay's but still subject to change.
All selectors live as constants below — update them if the UI changes.
Debug screenshots are written to ./debug/vinted_<step>.png on failure.
"""

import asyncio
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import config

# ---------------------------------------------------------------------------
# Condition mapping
# Keys are normalised lowercase strings the bot accepts.
# Values are the visible labels on Vinted's form.
# ---------------------------------------------------------------------------

CONDITION_MAP: dict[str, str] = {
    "new_tags":    "New with tags",
    "new":         "New without tags",
    "very_good":   "Very good",
    "good":        "Good",
    "satisfactory":"Satisfactory",
    # Aliases
    "mint":        "New with tags",
    "near_mint":   "Very good",
    "used":        "Good",
    "played":      "Satisfactory",
}

_DEFAULT_CONDITION = "Good"

# Maps pokemaz inventory condition strings (excel_db / Supabase `condition` column)
# to CONDITION_MAP keys above. Shared by bot.py's /listvinted and the web dashboard's
# /listings/list-vinted route so both entry points agree on the mapping.
INVENTORY_CONDITION_MAP: dict[str, str] = {
    "Near mint or better": "near_mint",
    "Lightly played":      "very_good",
    "Moderately played":   "good",
    "Heavily played":      "satisfactory",
}

# ---------------------------------------------------------------------------
# Selectors
# ---------------------------------------------------------------------------

# Photo upload step
_SEL_PHOTO_TRIGGER  = 'label[for*="photo"], [data-testid="upload-photo-trigger"], .photo-upload__add'
_SEL_FILE_INPUT     = 'input[type="file"][accept*="image"], input[type="file"]'
_SEL_PHOTO_CONTINUE = 'button:has-text("Next"), button:has-text("Continue"), [data-testid="next-button"]'

# Title and description
_SEL_TITLE          = 'input[id="title"], input[data-testid="item-title"], input[name="title"], input[placeholder*="title" i]'
_SEL_DESCRIPTION    = 'textarea[id="description"], textarea[data-testid="item-description"], textarea[name="description"]'

# Category — Vinted's category field is a search box, not a flat list: clicking
# the closed input opens a panel whose *first* level is top-level departments
# (Women, Men, Home, Hobbies & collectables, ...). "Single trading cards" only
# appears after typing into the inner "Find a category" search box, as one of
# several matching leaf categories. See _select_category / _click_dropdown_option.
_SEL_CATEGORY_INPUT  = "[placeholder='Select a category']"
_SEL_CATEGORY_SEARCH = "[placeholder='Find a category']"
_TARGET_CATEGORY     = "Single trading cards"

# Condition — same search-free dropdown pattern as category, opened via the
# input's placeholder (its rendered label text is NOT part of the DOM text,
# so a `text=` selector on the placeholder string never matches).
_SEL_CONDITION_INPUT = "[placeholder='Select condition']"

# Brand
_SEL_BRAND = 'input[data-testid="brand-input"], input[name="brand"], input[placeholder*="brand" i]'

# Price
_SEL_PRICE = 'input[data-testid="price-input"], input[id*="price"], input[name*="price"]'

# Submit
_SEL_SUBMIT = 'button[data-testid="submit-item"], button:has-text("Upload"), button:has-text("List"), button[type="submit"]:has-text("Post")'

# GDPR consent / other benign consent banners that can cover part of the form
_SEL_GDPR = 'button[id*="accept"], button:has-text("Accept all"), button:has-text("I accept")'
_SEL_BENIGN_OVERLAYS = [
    _SEL_GDPR,
    "#onetrust-accept-btn-handler",
    'button:has-text("Got it")',
]

# Login detection
_LOGIN_URL_HINTS = ("/login", "/signin", "/auth")

# Vinted's own hard-failure dialog ("Sorry, something went wrong / Try
# refreshing the page"). It has no close button and no backdrop-dismiss —
# in practice it has shown up correlated with rapid repeated automated
# requests, immediately followed by the session being logged out entirely.
# It is NOT force-clicked through: whatever caused it is a server-side
# failure, not a rendering glitch, and punching past a block that looks like
# an abuse-detection response is more likely to get the account flagged
# harder than to fix anything. See _check_for_critical_error.
_CRITICAL_ERROR_TEXT = "Sorry, something went wrong"

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ListingResult:
    platform:    str
    success:     bool
    listing_url: Optional[str] = None
    error:       Optional[str] = None


# ---------------------------------------------------------------------------
# Stealth helpers
# ---------------------------------------------------------------------------

_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] });
Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en-US', 'en'] });
if (!window.chrome) { window.chrome = { runtime: {} }; }
"""


async def _apply_stealth(context) -> None:
    try:
        from playwright_stealth import stealth_async
        context._stealth_fn = stealth_async
    except ImportError:
        context._stealth_fn = None
    await context.add_init_script(_STEALTH_JS)


async def _apply_stealth_to_page(page, context) -> None:
    fn = getattr(context, "_stealth_fn", None)
    if fn:
        try:
            await fn(page)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Typing helper
# ---------------------------------------------------------------------------

async def _type_human(page, selector: str, text: str) -> None:
    await page.click(selector)
    await page.fill(selector, "")
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.06, 0.16))


# ---------------------------------------------------------------------------
# Debug screenshot
# ---------------------------------------------------------------------------

async def _screenshot(page, step: str) -> None:
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    path = debug_dir / f"vinted_{step}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        print(f"[vinted] Debug screenshot → {path}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GDPR / Cookie consent
# ---------------------------------------------------------------------------

async def _dismiss_gdpr(page) -> None:
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        await page.locator(_SEL_GDPR).first.click(timeout=4_000)
        await page.wait_for_timeout(400)
    except PWTimeout:
        pass


async def _dismiss_overlays(page) -> None:
    """
    Dismiss benign consent/notice banners (cookie/GDPR, "got it" toasts) that
    can reappear or linger mid-flow and cover part of the form. Deliberately
    does NOT touch Vinted's critical-error dialog — see _check_for_critical_error.
    """
    for selector in _SEL_BENIGN_OVERLAYS:
        try:
            btn = page.locator(selector).first
            if await btn.count() > 0 and await btn.is_visible():
                await btn.click(timeout=2_000)
                print(f"[vinted] Dismissed banner: {selector}")
                await page.wait_for_timeout(400)
        except Exception:
            continue


async def _check_for_critical_error(page) -> None:
    """
    Raise RuntimeError immediately if Vinted's "Sorry, something went wrong"
    dialog is showing. Failing fast here avoids the alternative — every
    subsequent click silently retrying against a blocked, intercepted
    subtree for 30 seconds before timing out with a wall of unhelpful log
    output — and avoids hammering an already-failing session further.
    """
    try:
        dialog = page.locator(f'text="{_CRITICAL_ERROR_TEXT}"').first
        if await dialog.count() > 0 and await dialog.is_visible():
            await _screenshot(page, "critical_error_dialog")
            raise RuntimeError(
                f"Vinted returned a critical error ({_CRITICAL_ERROR_TEXT!r}). This has "
                "previously shown up after rapid repeated automated requests, followed by "
                "the session being logged out — likely rate-limiting/abuse detection. "
                "Wait before retrying, or refresh cookies with import_cookies.py if it "
                "persists. See debug/vinted_critical_error_dialog.png"
            )
    except RuntimeError:
        raise
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Session check
# ---------------------------------------------------------------------------

async def _check_session(page) -> None:
    """
    Raise PermissionError if Vinted has redirected to a login/sign-up page or
    if the page still shows the logged-out homepage (top bar has "Log in" / "Sign up").

    Checks both the URL and page content so the bot catches the case where
    Vinted silently lands on the homepage instead of /items/new.
    """
    url = page.url
    if any(h in url for h in _LOGIN_URL_HINTS):
        raise PermissionError(
            "[vinted] Session expired — please update browser_state/vinted_state.json "
            "by running: python import_cookies.py"
        )

    # Detect the logged-out homepage: look for Log-in / Sign-up links in the nav
    _LOGGED_OUT_INDICATORS = [
        'a:has-text("Log in")',
        'a:has-text("Sign up")',
        'button:has-text("Log in")',
        '[data-testid="header-login-button"]',
        '[data-testid="header-signup-button"]',
    ]
    for sel in _LOGGED_OUT_INDICATORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible():
                raise PermissionError(
                    "[vinted] Session expired — bot landed on the logged-out homepage. "
                    "Please update browser_state/vinted_state.json by running: "
                    "python import_cookies.py"
                )
        except PermissionError:
            raise
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------

async def _navigate_to_new_item(page) -> None:
    await page.goto(
        "https://www.vinted.co.uk/items/new",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    await _dismiss_gdpr(page)
    await _check_session(page)


async def _upload_photos(page, image_paths: list[Path]) -> None:
    """
    Upload photos on Vinted's first listing step.
    Vinted hides its file input — we look for it directly or trigger the upload
    area to expose it, then supply all image paths at once.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    if not image_paths:
        return

    await _check_for_critical_error(page)
    await _dismiss_overlays(page)

    # Try to find the file input directly (may be hidden)
    file_input = page.locator(_SEL_FILE_INPUT).first

    try:
        await file_input.wait_for(state="attached", timeout=8_000)
    except PWTimeout:
        # Click the visible trigger to expose the hidden input
        try:
            await page.locator(_SEL_PHOTO_TRIGGER).first.click(timeout=6_000)
            await page.wait_for_timeout(600)
            file_input = page.locator(_SEL_FILE_INPUT).first
            await file_input.wait_for(state="attached", timeout=6_000)
        except PWTimeout:
            await _screenshot(page, "upload_photos_fail")
            raise RuntimeError("Could not locate Vinted's photo upload input.")

    # Vinted allows up to 20 photos
    paths_to_upload = [str(p) for p in image_paths[:20]]
    await file_input.set_input_files(paths_to_upload)

    # Wait for upload progress indicators to clear
    await page.wait_for_timeout(random.uniform(2_000, 4_000))

    # Click "Next / Continue" if the wizard requires it after photos
    try:
        next_btn = page.locator(_SEL_PHOTO_CONTINUE).first
        await next_btn.click(timeout=5_000)
        await page.wait_for_load_state("domcontentloaded")
    except PWTimeout:
        pass  # No explicit next button — form is on the same page


async def _fill_title_and_description(page, item_name: str, description: str) -> None:
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        await page.wait_for_selector(_SEL_TITLE, timeout=12_000)
        await _type_human(page, _SEL_TITLE, item_name[:60])  # Vinted title limit: 60 chars
    except PWTimeout:
        await _screenshot(page, "fill_title_fail")
        raise RuntimeError("Could not find Vinted's title field.")

    if description:
        try:
            await page.wait_for_selector(_SEL_DESCRIPTION, timeout=5_000)
            await _type_human(page, _SEL_DESCRIPTION, description)
        except PWTimeout:
            pass  # Description field optional


async def _click_dropdown_option(page, label: str) -> bool:
    """
    Click a Vinted dropdown option row whose *first line* of text exactly
    matches `label` (case-insensitive).

    Vinted renders both the category-search results and the condition list as
    `div[role="button"]` rows shaped "Title\\nDescription/breadcrumb" (e.g.
    "Very good\\nA lightly used item..."). A plain substring/:has-text() match
    is not safe here — "Good" is a substring of "Very good", so it can select
    the wrong row. Exact first-line matching avoids that; a substring fallback
    is kept for resilience if Vinted's copy changes slightly.
    """
    candidates = page.locator('div[role="button"]')
    count = await candidates.count()
    target = label.strip().lower()
    for i in range(count):
        el = candidates.nth(i)
        try:
            text = await el.inner_text()
        except Exception:
            continue
        first_line = text.split("\n", 1)[0].strip().lower()
        if first_line == target:
            await el.click()
            return True

    fallback = page.locator(f'div[role="button"]:has-text("{label}")').first
    if await fallback.count() > 0:
        await fallback.click()
        return True
    return False


async def _select_category(page, item_name: str) -> None:
    """
    Select the Vinted sell-form category.

    The category field is a search box, not a flat option list: opening it
    shows top-level departments (Women, Men, Home, ...), and "Single trading
    cards" only appears as a search result after typing into the inner
    "Find a category" box that appears once the panel is open.
    """
    await _check_for_critical_error(page)
    await _dismiss_overlays(page)

    await page.evaluate("window.scrollBy(0, 300)")
    await page.wait_for_timeout(500)

    cat_input = page.locator(_SEL_CATEGORY_INPUT).first
    if await cat_input.count() == 0:
        print("[vinted] WARNING: Category input not found — skipping (non-fatal).")
        return
    await cat_input.click()
    await page.wait_for_timeout(800)

    search_box = page.locator(_SEL_CATEGORY_SEARCH).first
    if await search_box.count() > 0:
        await search_box.fill(_TARGET_CATEGORY)
        await page.wait_for_timeout(1_200)

    # Mid-step screenshot — key diagnostic
    Path("debug").mkdir(exist_ok=True)
    await page.screenshot(path="debug/vinted_cat_open.png")
    print("[vinted] Debug screenshot → debug/vinted_cat_open.png")

    if await _click_dropdown_option(page, _TARGET_CATEGORY):
        print(f"[vinted] Category selected: {_TARGET_CATEGORY}")
    else:
        print(f"[vinted] WARNING: Could not find {_TARGET_CATEGORY!r} — category not set")

    await page.wait_for_timeout(500)

    # Some Vinted flows show a separate confirm button after picking a result.
    confirm = page.locator(
        "button:has-text('Confirm'), button:has-text('Done'), button:has-text('Select')"
    ).first
    if await confirm.count() > 0:
        await confirm.click()
        await page.wait_for_timeout(500)


async def _set_condition(page, condition_label: str) -> None:
    """
    Select the condition from Vinted's "Select condition" dropdown.
    Non-fatal — logs a warning and returns if not found.
    """
    await _check_for_critical_error(page)
    await _dismiss_overlays(page)

    await _screenshot(page, "before_condition")

    cond_input = page.locator(_SEL_CONDITION_INPUT).first
    if await cond_input.count() == 0:
        print("[vinted] WARNING: Condition field not found — skipping (non-fatal).")
        return
    await cond_input.scroll_into_view_if_needed()
    await cond_input.click()
    await page.wait_for_timeout(800)

    if await _click_dropdown_option(page, condition_label):
        print(f"[vinted] Condition set: {condition_label}")
        await page.wait_for_timeout(500)
    else:
        print(f"[vinted] WARNING: Condition '{condition_label}' not found — skipping (non-fatal).")


async def _fill_brand(page, brand: str) -> None:
    if not brand:
        return
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        await page.wait_for_selector(_SEL_BRAND, timeout=4_000)
        await _type_human(page, _SEL_BRAND, brand)
        # Select the first autocomplete suggestion
        await page.wait_for_timeout(800)
        await page.keyboard.press("ArrowDown")
        await page.keyboard.press("Enter")
    except PWTimeout:
        pass


async def _set_price(page, price_gbp: float) -> None:
    await _check_for_critical_error(page)
    await _dismiss_overlays(page)

    await page.evaluate("window.scrollBy(0, 300)")
    await page.wait_for_timeout(500)

    price_input = None
    for sel in [
        "input[placeholder*='0.00']",
        "input[type='number']",
        "input[id*='price']",
        "input[name*='price']",
        _SEL_PRICE,
    ]:
        el = page.locator(sel).first
        if await el.count() > 0:
            price_input = el
            break

    if price_input is not None:
        await price_input.click(click_count=3)
        await price_input.fill(f"{price_gbp:.2f}")
        await page.wait_for_timeout(500)
        print(f"[vinted] Price set: £{price_gbp:.2f}")
    else:
        await _screenshot(page, "set_price_fail")
        print("[vinted] WARNING: Price field not found")


_SUBMIT_SELECTORS = [
    _SEL_SUBMIT,
    '[data-testid="submit-button"]',
    'button:has-text("Upload")',
    'button:has-text("Publish")',
    'button[type="submit"]',
]


async def _find_submit_button(page):
    """Try each submit-button selector, returning the first visible + enabled match."""
    for selector in _SUBMIT_SELECTORS:
        try:
            candidate = page.locator(selector).first
            if await candidate.count() == 0:
                continue
            if await candidate.is_visible() and await candidate.is_enabled():
                print(f"[vinted] Found submit button: {selector}")
                return candidate
        except Exception:
            continue
    return None


async def _submit_and_get_url(page, dry_run: bool) -> Optional[str]:
    Path("debug").mkdir(exist_ok=True)
    await _check_for_critical_error(page)
    await _dismiss_overlays(page)

    if dry_run:
        await page.screenshot(path="debug/vinted_dry_run_final.png", full_page=True)
        print("[vinted] dry_run=True — stopping before submission.")
        return None

    # Take a full-page screenshot of the completed form before submitting
    await page.screenshot(path="debug/vinted_ready_to_submit.png", full_page=True)
    print("[vinted] Form filled — screenshot saved to debug/vinted_ready_to_submit.png")

    submit_btn = await _find_submit_button(page)
    if not submit_btn:
        await _screenshot(page, "no_submit_btn")
        raise RuntimeError("Could not find Vinted submit button — see debug/vinted_no_submit_btn.png")

    # Human-like pacing before submitting — a click landing the instant the
    # form finishes filling is a bot tell that can trip Vinted's datadome check.
    await page.wait_for_timeout(random.randint(800, 1_500))
    await submit_btn.scroll_into_view_if_needed()
    await page.wait_for_timeout(random.randint(300, 600))
    await submit_btn.hover()
    await page.wait_for_timeout(random.randint(400, 800))

    before_url = page.url
    await submit_btn.click()

    try:
        await page.wait_for_url(
            lambda url: "/items/" in url and url != before_url,
            timeout=20_000,
        )
    except Exception:
        pass  # Fall through to the explicit URL/content check below

    final_url = page.url

    # A successful submit navigates to the new item's page (/items/<id>-<slug>).
    # /items/new is the form itself, so exclude it explicitly.
    if final_url != before_url and re.search(r"/items/\d+", final_url):
        print(f"[vinted] Listed successfully: {final_url}")
        return final_url

    # Still on the form — check for bot-detection first, then any inline
    # validation error Vinted surfaces, before giving up with a generic message.
    await _screenshot(page, "post_submit")

    content = ""
    try:
        content = (await page.content()).lower()
    except Exception:
        pass
    if "datadome" in content or "captcha" in content:
        raise RuntimeError(
            "Vinted bot detection triggered on submit — try refreshing cookies "
            "with import_cookies.py (see debug/vinted_post_submit.png)"
        )

    error_text = ""
    try:
        error_el = page.locator('[class*="error" i], [role="alert"]').first
        if await error_el.count() > 0 and await error_el.is_visible():
            error_text = (await error_el.inner_text()).strip()
    except Exception:
        pass

    detail = f" — {error_text}" if error_text else ""
    raise RuntimeError(
        f"Vinted submit did not reach a listing page (still at {final_url}){detail} "
        "— see debug/vinted_post_submit.png"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def list_item_on_vinted(
    item_name:   str,
    price_gbp:   float,
    image_paths: list[Path],
    condition:   str = "good",
    description: str = "",
    brand:       str = "",
    dry_run:     bool = False,
) -> ListingResult:
    """
    Create a new Vinted UK listing and return a ListingResult.

    Parameters
    ----------
    item_name   : Listing title (truncated to 60 chars by Vinted).
    price_gbp   : Listing price in GBP.
    image_paths : Local image files to upload (max 20 used).
    condition   : Key from CONDITION_MAP, e.g. "good", "very_good", "new".
    description : Item description (optional).
    brand       : Brand name (optional, autocomplete).
    dry_run     : If True, fill everything but do not click Submit.
    """
    state_path = Path(config.VINTED_STATE_PATH)
    if not state_path.exists():
        return ListingResult(
            platform="Vinted",
            success=False,
            error=(
                f"No saved session found at {state_path}. "
                "Run: python generate_cookies.py vinted"
            ),
        )

    condition_label = CONDITION_MAP.get(condition.lower(), _DEFAULT_CONDITION)

    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=config.VINTED_HEADLESS,
                slow_mo=0 if config.VINTED_HEADLESS else 50,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
                    "--disable-dev-shm-usage",
                    "--window-size=1366,768",
                ],
            )
            context = await browser.new_context(
                storage_state=str(state_path),
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                locale="en-GB",
                timezone_id="Europe/London",
                viewport={"width": 1366, "height": 768},
                color_scheme="light",
                extra_http_headers={
                    "Accept-Language":    "en-GB,en;q=0.9",
                    "Accept":             "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                    "Upgrade-Insecure-Requests": "1",
                    "DNT":                "1",
                    "sec-ch-ua":          '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
                    "sec-ch-ua-mobile":   "?0",
                    "sec-ch-ua-platform": '"Windows"',
                },
            )
            await _apply_stealth(context)

            page = await context.new_page()
            await _apply_stealth_to_page(page, context)

            try:
                await _navigate_to_new_item(page)
                await _upload_photos(page, image_paths)
                await _fill_title_and_description(page, item_name, description)
                Path("debug").mkdir(exist_ok=True)
                await _select_category(page, item_name)

                await page.wait_for_timeout(1_000)
                await _check_for_critical_error(page)

                await page.screenshot(path="debug/vinted_after_category.png")
                print("[vinted] Debug screenshot → debug/vinted_after_category.png")
                await _set_condition(page, condition_label)
                await _fill_brand(page, brand)
                await _set_price(page, price_gbp)
                await page.screenshot(path="debug/vinted_after_price.png")
                print("[vinted] Debug screenshot → debug/vinted_after_price.png")

                await page.wait_for_timeout(random.uniform(500, 900))

                listing_url = await _submit_and_get_url(page, dry_run)

            except (PermissionError, RuntimeError) as exc:
                await _screenshot(page, "fatal_error")
                raise exc
            finally:
                await context.close()
                await browser.close()

        return ListingResult(
            platform="Vinted",
            success=True,
            listing_url=listing_url,
        )

    except PermissionError as exc:
        return ListingResult(platform="Vinted", success=False, error=str(exc))
    except Exception as exc:
        return ListingResult(
            platform="Vinted",
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )
