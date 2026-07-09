"""
lister_ebay.py — async eBay UK listing automation via Playwright.

Public interface
----------------
    result = await list_item_on_ebay(
        item_name   = "Charizard Holo Base Set",
        price_gbp   = 45.00,
        image_paths = [Path("temp_images/item_5/0.jpg"), ...],
        condition   = "Used",           # optional
        description = "Near mint...",   # optional
        dry_run     = False,            # True → stop before clicking Submit
    )

    result.success      → bool
    result.listing_url  → str | None   (eBay item URL if published)
    result.error        → str | None   (human-readable error if failed)

Authentication
--------------
Loads saved browser state from config.EBAY_STATE_PATH.
Run generate_cookies.py ebay first to create that file.

Selector stability
------------------
eBay's listing form uses React and changes periodically.  All selectors are
defined as constants at the top of this file so they're easy to update.
If a step fails, a debug screenshot is written to ./debug/ebay_<step>.png.

Stealth
-------
Applies playwright-stealth (if installed) plus manual navigator.webdriver
removal and randomised character-by-character typing to pass bot detection.
"""

import asyncio
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import config

# ---------------------------------------------------------------------------
# Selectors  (update here if eBay changes their UI)
# ---------------------------------------------------------------------------

# --- Sell-entry page ("What are you selling?") ---
_SEL_ENTRY_KEYWORD   = 'input[data-testid="keyword-input"], input[aria-label*="sell" i], input[placeholder*="selling" i]'
_SEL_ENTRY_SUBMIT    = 'button[data-testid="keyword-submit"], button:has-text("Get started"), button:has-text("Start listing")'
_SEL_CATEGORY_ITEM   = 'li[data-testid="category-item"] button, .category-list li button, ul.cat-results li button'

# --- Promotional dashboard (shown to returning sellers before the form) ---
# eBay sometimes renders a "It's free to sell" landing page instead of going
# straight to the listing form.  Any of these CTAs bypass it.
_SEL_PROMO_CTA = (
    'a:has-text("List an item"),'
    'button:has-text("List an item"),'
    'a:has-text("Sell for free"),'
    'button:has-text("Sell for free"),'
    'a:has-text("Start selling"),'
    'button:has-text("Start selling"),'
    '[data-testid="list-item-cta"],'
    '[data-testid="sell-cta"]'
)

# --- Listing form ---
_SEL_TITLE           = (
    'input[data-testid="title-input"],'
    'input[placeholder*="Title" i],'
    'input[aria-label*="Title" i],'
    '#title,'
    '#c_mlt #body_title input,'
    'textarea[name="title"]'
)
_SEL_PHOTO_AREA      = '[data-testid="photo-picker"], .photo-tile-container, .add-photos-area, #pht-btn, input[type="file"][accept*="image"]'
_SEL_FILE_INPUT      = 'input[type="file"]'
_SEL_CONDITION       = 'select[data-testid="condition-select"], select[id*="condition"], [data-testid="condition-dropdown"] select'
_SEL_DESCRIPTION     = 'iframe[id*="desc"], iframe[title*="description" i], textarea[id*="description"]'
_SEL_PRICE           = 'input[data-testid="price-input"], input[id*="price"], input[name*="price"]'
_SEL_SUBMIT          = 'button[data-testid="SUBMIT"], button:has-text("List it"), button:has-text("Publish"), button:has-text("Submit listing")'
_SEL_GDPR            = 'button#gdpr-banner-accept, button[name="accept"], button:has-text("Accept all")'

# --- Catalog-match page ("Find a match" / "Continue without match") ---
# eBay may show a product-catalogue interstitial after category selection.
_SEL_CATALOG_SKIP = (
    'button:has-text("Continue without match"),'
    'a:has-text("Continue without match"),'
    'button:has-text("Don\'t use a match"),'
    'a:has-text("Don\'t use a match"),'
    '[data-testid="SKIP"],'
    '[data-testid="continue-without-match"],'
    '[data-testid="skip-catalog-match"]'
)

# --- Post-submission ---
_SEL_LISTING_LINK    = 'a[href*="/itm/"], a[data-testid="listing-link"], .confirmation-url a'

# URL fragments that indicate the session has expired
_LOGIN_URL_HINTS     = ("signin.ebay.co.uk", "/signin/", "/login")

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
// Remove the primary automation indicator
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

// Spoof plugin count (zero plugins = headless flag)
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });

// Spoof language list
Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en-US', 'en'] });

// Inject chrome runtime object (absent in headless = red flag)
if (!window.chrome) { window.chrome = { runtime: {} }; }

// Override permissions.query so it doesn't expose automation
const _origPermQuery = navigator.permissions.query.bind(navigator.permissions);
navigator.permissions.query = (p) =>
    p.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : _origPermQuery(p);
"""


async def _apply_stealth(context) -> None:
    """Apply stealth patches to the whole browser context."""
    # playwright-stealth package (preferred — covers more fingerprints)
    try:
        from playwright_stealth import stealth_async
        # stealth_async operates on a Page; we'll apply it per-page via init script
        # Store the function so _new_stealth_page can use it
        context._stealth_fn = stealth_async
    except ImportError:
        context._stealth_fn = None

    # Always inject the manual JS regardless
    await context.add_init_script(_STEALTH_JS)


async def _apply_stealth_to_page(page, context) -> None:
    stealth_fn = getattr(context, "_stealth_fn", None)
    if stealth_fn is not None:
        try:
            await stealth_fn(page)
        except Exception:
            pass  # graceful degradation


# ---------------------------------------------------------------------------
# Typing helper
# ---------------------------------------------------------------------------

async def _type_human(page, selector: str, text: str) -> None:
    """
    Click the field and type each character with a random 60–160 ms delay.
    Much harder to detect as automation than page.type() with uniform timing.
    """
    await page.click(selector)
    await page.fill(selector, "")          # clear existing content
    for char in text:
        await page.keyboard.type(char)
        await asyncio.sleep(random.uniform(0.06, 0.16))


# ---------------------------------------------------------------------------
# Debug screenshot
# ---------------------------------------------------------------------------

async def _screenshot(page, step: str) -> None:
    debug_dir = Path("debug")
    debug_dir.mkdir(exist_ok=True)
    path = debug_dir / f"ebay_{step}.png"
    try:
        await page.screenshot(path=str(path), full_page=True)
        print(f"[ebay] Debug screenshot → {path}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# GDPR / Cookie consent
# ---------------------------------------------------------------------------

async def _dismiss_gdpr(page) -> None:
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        await page.locator(_SEL_GDPR).first.click(timeout=4_000)
        await page.wait_for_timeout(500)
    except PWTimeout:
        pass


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------

async def _check_session(page) -> None:
    """Raise if the page has redirected to the login flow."""
    url = page.url
    if any(hint in url for hint in _LOGIN_URL_HINTS):
        raise PermissionError(
            "eBay session has expired — run `python generate_cookies.py ebay` to refresh."
        )


async def _navigate_to_sell(page) -> None:
    from playwright.async_api import TimeoutError as PWTimeout

    # Go directly to the keyword-entry / category-suggest page, bypassing the
    # Seller Hub dashboard that otherwise intercepts the flow.
    await page.goto(
        "https://www.ebay.co.uk/sl/prelist/suggest?sr=kw",
        wait_until="domcontentloaded",
        timeout=30_000,
    )
    # Hard settle: React needs time to fully hydrate after initial load.
    # Without this, Playwright probes the DOM before eBay's scripts have mounted
    # their components, which crashes the tab ("Target closed").
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(3_000)

    # Debug screenshot — shows exactly what eBay served after navigation.
    await _screenshot(page, "landing")

    await _dismiss_gdpr(page)
    await _check_session(page)
    await _dismiss_draft_modal(page)
    await _dismiss_promo_if_present(page)


async def _dismiss_draft_modal(page) -> None:
    """
    Dismiss eBay's 'Resume your draft' modal that appears when a previous
    session crashed mid-flow.  Clicking 'Start a new listing' discards the
    stale draft and returns to the keyword-entry page.  Non-fatal if absent.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    _DRAFT_BTN = (
        'button:has-text("Start a new listing"),'
        'button:has-text("Create new listing"),'
        'button:has-text("Discard draft"),'
        'a:has-text("Start a new listing")'
    )
    try:
        btn = page.locator(_DRAFT_BTN).first
        await btn.wait_for(state="visible", timeout=2_000)
        await btn.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(2_000)
        print("[ebay] Draft modal dismissed — starting fresh listing.")
    except PWTimeout:
        pass  # no draft modal present


async def _dismiss_promo_if_present(page) -> None:
    """
    Click through eBay's promotional dashboard if it appears instead of the
    listing form ("It's now free to sell" / "List an item" landing page).

    Non-fatal: if no promo CTA is found we assume we're already on the right
    page and let the next step's wait_for_selector surface any real error.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    # Let eBay's heavy JS bundle settle before touching the DOM.
    # Without this pause the page crashes ("Target closed") when Playwright
    # interacts with elements that eBay's bot-detection scripts haven't
    # finished inspecting yet.
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(2_000)

    # Fast check: if the listing keyword input is already visible, we're good
    try:
        await page.wait_for_selector(_SEL_ENTRY_KEYWORD, timeout=3_000)
        return
    except PWTimeout:
        pass

    # Keyword input wasn't there — look for a promo CTA to click through
    try:
        cta = page.locator(_SEL_PROMO_CTA).first
        await cta.wait_for(state="visible", timeout=6_000)
        await cta.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(random.randint(2_000, 4_500))
        print("[ebay] Promo dashboard dismissed — navigated to listing form.")
    except PWTimeout:
        # Neither keyword input nor promo CTA found; proceed and let the
        # category step decide whether we're on the listing form already
        print("[ebay] No promo CTA found; proceeding to category step.")


async def _select_category(page, item_name: str) -> None:
    """
    Fill the 'What are you selling?' search box and accept the first category.
    If eBay skips this step (returning seller), continue silently.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    try:
        await page.wait_for_selector(_SEL_ENTRY_KEYWORD, timeout=8_000)
    except PWTimeout:
        # eBay may have taken us straight to the listing form — that's fine
        print("[ebay] Category search step not found; assuming direct listing form.")
        return

    await _type_human(page, _SEL_ENTRY_KEYWORD, item_name)
    await page.wait_for_timeout(random.uniform(800, 1400))

    # Try pressing Enter or clicking the search button
    try:
        submit = page.locator(_SEL_ENTRY_SUBMIT).first
        await submit.click(timeout=4_000)
    except PWTimeout:
        await page.keyboard.press("Enter")

    await page.wait_for_load_state("domcontentloaded")

    # Select the first suggested category if a list appears
    try:
        first_cat = page.locator(_SEL_CATEGORY_ITEM).first
        await first_cat.click(timeout=6_000)
        await page.wait_for_load_state("domcontentloaded")
        await page.wait_for_timeout(random.randint(2_000, 4_500))
    except PWTimeout:
        pass  # no category list appeared — eBay already chose one


async def _search_and_select_catalog(page, title: str) -> None:
    """
    On eBay's 'Find a match' catalog page, search for the item and select the
    first result so eBay auto-fills item specifics.

    If no catalog entry exists for this card, falls back to
    'Continue without match' and preserves the manual-entry flow.
    Non-fatal if the catalog step was skipped by eBay entirely.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    _MATCH_BTN = (
        'button:has-text("Select"),'
        'button:has-text("Sell one like this"),'
        'button:has-text("Sell yours")'
    )

    # Fast path: listing form already visible — catalog step was silently skipped
    try:
        await page.wait_for_selector(_SEL_TITLE, timeout=4_000)
        print("[ebay] Catalog step not present; proceeding to listing form.")
        return
    except PWTimeout:
        pass

    # Search the catalog for this item
    try:
        search_input = page.locator(
            '.textbox__control, input#kw, input[role="combobox"], input[placeholder*="selling" i]'
        ).first
        await search_input.wait_for(state="visible", timeout=10_000)
        await search_input.focus()
        await page.wait_for_timeout(500)

        # press_sequentially fires real keydown/keypress/keyup events that
        # React's synthetic onChange handler actually picks up
        await search_input.press_sequentially(title[:80], delay=50)
        await page.wait_for_timeout(1_000)

        await search_input.press("Enter")
        await page.wait_for_timeout(2_000)

        # Fallback: click the search button if Enter didn't trigger navigation
        search_btn = page.locator(
            'button.search-button, button:has-text("Search"), button[aria-label="Search"]'
        )
        if await search_btn.is_visible():
            await search_btn.first.click()

        await page.wait_for_timeout(1_000)
        print(f"[ebay] Catalog search submitted: {title[:40]!r}")
    except PWTimeout:
        print("[ebay] Catalog search input not found; attempting skip.")

    # Select the first catalog match to auto-fill item specifics
    try:
        match_btn = page.locator(_MATCH_BTN).first
        await match_btn.click(timeout=5_000)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(random.randint(2_000, 4_500))
        print("[ebay] Catalog match selected — item specifics auto-filled.")
        return
    except PWTimeout:
        print("[ebay] No catalog match found — falling back to 'Continue without match'.")

    # Fallback: no catalog entry for this card
    try:
        skip = page.locator(_SEL_CATALOG_SKIP).first
        await skip.wait_for(state="visible", timeout=8_000)
        await skip.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(random.randint(2_000, 4_500))
        print("[ebay] Catalog match skipped ('Continue without match').")
    except PWTimeout:
        print("[ebay] Neither catalog match nor skip button found; proceeding anyway.")


async def _handle_graded_interstitial(page, condition: str) -> None:
    """
    Handle eBay's Graded / Ungraded selection page that sometimes appears
    after the catalogue-match step and before the main listing form.

    Selects "Graded" when the supplied condition indicates a graded card
    (PSA / BGS / CGC / graded), otherwise selects "Ungraded".  Then clicks
    the Continue button to proceed.  Non-fatal if the interstitial is absent.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    # Fast path — if the title input is already visible, this step is a no-op
    try:
        await page.wait_for_selector(_SEL_TITLE, timeout=3_000)
        return
    except PWTimeout:
        pass

    grade = (
        "Graded"
        if condition.lower() in ("graded", "psa", "bgs", "cgc", "graded card")
        else "Ungraded"
    )

    _GRADE_SEL = (
        f'label:has-text("{grade}"),'
        f'button:has-text("{grade}"),'
        f'[data-value="{grade.lower()}"],'
        f'[data-testid*="{grade.lower()}"]'
    )
    _CONTINUE_SEL = (
        'button:has-text("Continue"),'
        'button[data-testid="continue"],'
        'button[data-testid="CONTINUE"],'
        'a:has-text("Continue")'
    )

    # Check whether the interstitial is actually on screen
    try:
        option = page.locator(_GRADE_SEL).first
        await option.wait_for(state="visible", timeout=5_000)
    except PWTimeout:
        print("[ebay] Graded/Ungraded interstitial not present; proceeding.")
        return

    await option.click(force=True)
    await page.wait_for_timeout(random.randint(1_500, 3_000))
    print(f"[ebay] Graded/Ungraded interstitial: selected '{grade}'.")

    try:
        cont = page.locator(_CONTINUE_SEL).first
        await cont.wait_for(state="visible", timeout=5_000)
        await cont.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(random.randint(2_000, 4_500))
        print("[ebay] Graded/Ungraded interstitial: 'Continue' clicked.")
    except PWTimeout:
        print("[ebay] Graded/Ungraded interstitial: 'Continue' button not found.")


async def _handle_specific_condition(page, condition: str) -> None:
    """
    Handle eBay's second condition interstitial ("Select ungraded condition" /
    "Select graded condition") that appears after the Graded/Ungraded screen.

    Clicks the tile whose text matches the supplied condition string, then
    clicks Continue to reach the main listing form.  Non-fatal if absent.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    # Fast path — listing form already loaded
    try:
        await page.wait_for_selector(_SEL_TITLE, timeout=3_000)
        return
    except PWTimeout:
        pass

    _CONTINUE_SEL = (
        'button:has-text("Continue"),'
        'button[data-testid="continue"],'
        'button[data-testid="CONTINUE"],'
        'a:has-text("Continue")'
    )

    # Check that this interstitial is actually on screen by looking for
    # the Continue button (present on both the graded and ungraded variants)
    try:
        await page.wait_for_selector(_CONTINUE_SEL, timeout=5_000)
    except PWTimeout:
        print("[ebay] Specific-condition interstitial not present; proceeding.")
        return

    # Scope the match to interactive container elements only (button / label /
    # div[role="button"]) so nested sub-text like "Comparable to a fresh pack"
    # inside the tile doesn't cause a false-positive match on the wrong element.
    try:
        tile = (
            page.locator('button, div[role="button"], label')
                .filter(has_text=condition)
                .first
        )
        await tile.wait_for(state="visible", timeout=4_000)
        await tile.click(force=True)
        await page.wait_for_timeout(random.randint(1_500, 3_000))
        print(f"[ebay] Specific-condition interstitial: selected '{condition}'.")
    except Exception as exc:
        print(f"[ebay] Specific-condition interstitial: could not select '{condition}': {exc}")

    try:
        cont = page.locator(_CONTINUE_SEL).first
        await cont.wait_for(state="visible", timeout=5_000)
        await cont.click()
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await page.wait_for_timeout(random.randint(2_000, 4_500))
        print("[ebay] Specific-condition interstitial: 'Continue' clicked.")
    except PWTimeout:
        print("[ebay] Specific-condition interstitial: 'Continue' button not found.")


async def _check_for_captcha(page) -> None:
    """
    Raise immediately if eBay has served an hCaptcha challenge page.

    Checked selectors (any one visible = CAPTCHA confirmed):
      • iframe whose src contains 'hcaptcha'
      • any element whose text contains 'verify yourself' (case-insensitive)
      • any element whose text contains 'hcaptcha' (case-insensitive)

    Raises
    ------
    RuntimeError  with a message that tells the user exactly what to do next.
    """
    _CAPTCHA_SELECTORS = [
        'iframe[src*="hcaptcha"]',
        'iframe[data-hcaptcha-widget-id]',
        'text=/verify yourself/i',
        'text=/hcaptcha/i',
        '[class*="hcaptcha"]',
        '[id*="hcaptcha"]',
    ]
    for sel in _CAPTCHA_SELECTORS:
        try:
            el = page.locator(sel).first
            if await el.is_visible():
                await _screenshot(page, "captcha_detected")
                raise RuntimeError(
                    "[ebay] CAPTCHA triggered! Please update ebay_state.json "
                    "(run: python generate_cookies.py ebay) or wait before retrying."
                )
        except RuntimeError:
            raise
        except Exception:
            pass


async def _fill_title(page, item_name: str) -> None:
    """
    Wait for eBay's React form, then fill the title field.

    Strategy order:
      1. Wait for 'Complete your listing' heading to confirm the form mounted.
      2. get_by_role("textbox", name="Title") — React-friendly, pierces Shadow DOM.
      3. CSS attribute selectors as ordered fallbacks.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    # Let React finish rendering
    try:
        await page.wait_for_load_state("networkidle", timeout=15_000)
    except PWTimeout:
        pass

    # Bail out fast if eBay has served a CAPTCHA challenge instead of the form
    await _check_for_captcha(page)

    # Anchor: confirm the main form is visible before touching any field
    try:
        await page.wait_for_selector('text="Complete your listing"', timeout=10_000)
    except PWTimeout:
        pass  # heading absent on some account types; proceed anyway

    # ── Primary: get_by_role pierces Shadow DOM and matches React aria labels ──
    try:
        title_box = page.get_by_role("textbox", name="Title").first
        await title_box.wait_for(state="visible", timeout=6_000)
        await title_box.click()
        await title_box.fill("")
        for char in item_name[:80]:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.06, 0.16))
        print("[ebay] Title filled via get_by_role('textbox', name='Title').")
        return
    except PWTimeout:
        pass

    # ── Fallback: CSS attribute selectors ────────────────────────────────────
    _TITLE_SELECTORS = [
        'input[aria-label*="Title" i]',
        'input[placeholder*="Title" i]',
        'input[maxlength="80"]',
        'input[data-testid="title-input"]',
        '#title',
        '#c_mlt #body_title input',
        'textarea[name="title"]',
    ]

    for sel in _TITLE_SELECTORS:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=5_000)
            await el.click()
            await el.fill("")
            for char in item_name[:80]:
                await page.keyboard.type(char)
                await asyncio.sleep(random.uniform(0.06, 0.16))
            print(f"[ebay] Title filled via selector: {sel}")
            return
        except PWTimeout:
            continue

    await _screenshot(page, "fill_title_fail")
    raise RuntimeError("Could not find the listing title field.")


async def _upload_photos(page, image_paths: list[Path]) -> None:
    """
    Trigger eBay's file picker by clicking the photo area, then supply
    all image paths to the hidden file input.
    """
    from playwright.async_api import TimeoutError as PWTimeout

    if not image_paths:
        return

    # Try to locate a visible file input directly first
    try:
        file_input = page.locator(_SEL_FILE_INPUT).first
        await file_input.wait_for(state="attached", timeout=10_000)
    except PWTimeout:
        # Click the photo area to expose the file input
        try:
            await page.locator(_SEL_PHOTO_AREA).first.click(timeout=8_000)
            await page.wait_for_timeout(800)
            file_input = page.locator(_SEL_FILE_INPUT).first
            await file_input.wait_for(state="attached", timeout=6_000)
        except PWTimeout:
            await _screenshot(page, "upload_photos_fail")
            raise RuntimeError("Could not locate the eBay photo upload input.")

    # Upload up to 12 images (eBay's limit)
    paths_to_upload = [str(p) for p in image_paths[:12]]
    await file_input.set_input_files(paths_to_upload)

    # Wait for upload indicators to appear
    await page.wait_for_timeout(random.uniform(2_000, 3_500))


async def _set_condition(page, condition: str) -> None:
    """Select a condition from the dropdown. Silently skips if not found."""
    from playwright.async_api import TimeoutError as PWTimeout
    try:
        sel = page.locator(_SEL_CONDITION).first
        await sel.wait_for(state="visible", timeout=6_000)
        await sel.select_option(label=condition)
    except (PWTimeout, Exception):
        # Condition may not be a simple <select> or might not exist for this category
        pass


async def _fill_description(page, description: str) -> None:
    """Fill the description — handles both textarea and CKEditor iframe."""
    from playwright.async_api import TimeoutError as PWTimeout

    if not description:
        return

    # Check for an iframe-based editor (CKEditor)
    try:
        iframe_el = page.locator(_SEL_DESCRIPTION).first
        await iframe_el.wait_for(state="attached", timeout=5_000)
        frame = await iframe_el.content_frame()
        if frame:
            await frame.click("body")
            await frame.fill("body", description)
            return
    except (PWTimeout, Exception):
        pass

    # Fallback: plain textarea
    try:
        await page.fill('textarea[id*="description"], textarea[name*="description"]', description)
    except Exception:
        pass


async def _set_price(page, price_gbp: float) -> None:
    from playwright.async_api import TimeoutError as PWTimeout

    price_str = f"{price_gbp:.2f}"

    # Primary: get_by_role with aria-label match
    try:
        price_box = page.get_by_role("textbox", name=re.compile(r"price", re.I)).first
        await price_box.wait_for(state="visible", timeout=6_000)
        await price_box.click()
        await price_box.fill("")
        for char in price_str:
            await page.keyboard.type(char)
            await asyncio.sleep(random.uniform(0.06, 0.16))
        print("[ebay] Price filled via get_by_role.")
        return
    except PWTimeout:
        pass

    # Fallback: CSS selectors
    _PRICE_SELECTORS = [
        'input[aria-label*="price" i]',
        'input[data-testid="price-input"]',
        'input[id*="price"]',
        'input[name*="price"]',
    ]
    for sel in _PRICE_SELECTORS:
        try:
            el = page.locator(sel).first
            await el.wait_for(state="visible", timeout=5_000)
            await el.click()
            await el.fill("")
            for char in price_str:
                await page.keyboard.type(char)
                await asyncio.sleep(random.uniform(0.06, 0.16))
            print(f"[ebay] Price filled via selector: {sel}")
            return
        except PWTimeout:
            continue

    await _screenshot(page, "set_price_fail")
    raise RuntimeError("Could not find the listing price field.")


async def _submit_and_get_url(page, dry_run: bool) -> Optional[str]:
    """Click the publish button and extract the resulting listing URL."""
    from playwright.async_api import TimeoutError as PWTimeout

    if dry_run:
        await _screenshot(page, "dry_run_final")
        print("[ebay] dry_run=True — stopping before submission.")
        return None

    try:
        submit_btn = page.locator(_SEL_SUBMIT).first
        await submit_btn.wait_for(state="visible", timeout=10_000)
        await submit_btn.click()
    except PWTimeout:
        await _screenshot(page, "submit_fail")
        raise RuntimeError("Could not find or click the Submit listing button.")

    # Wait for the confirmation page to load
    await page.wait_for_load_state("domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2_000)

    # Try to extract the new listing URL
    listing_url: Optional[str] = None

    # Attempt 1: look for a direct link on the confirmation page
    try:
        link = page.locator(_SEL_LISTING_LINK).first
        href = await link.get_attribute("href", timeout=5_000)
        if href:
            listing_url = href if href.startswith("http") else f"https://www.ebay.co.uk{href}"
    except PWTimeout:
        pass

    # Attempt 2: the confirmation page URL itself may be the listing
    if not listing_url:
        current = page.url
        if "/itm/" in current:
            listing_url = current

    return listing_url


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def list_item_on_ebay(
    item_name:   str,
    price_gbp:   float,
    image_paths: list[Path],
    condition:   str = "Used",
    description: str = "",
    dry_run:     bool = False,
) -> ListingResult:
    """
    Create a new eBay UK listing and return a ListingResult.

    Parameters
    ----------
    item_name   : Listing title (truncated to 80 chars by eBay).
    price_gbp   : Buy-it-now price in GBP.
    image_paths : Local image files to upload (max 12 used).
    condition   : eBay condition label, e.g. "New", "Used", "For parts or not working".
    description : Item description text (optional).
    dry_run     : If True, fill everything but do not click Submit.
    """
    # Cookie / session injection — Playwright loads cookies, localStorage, and
    # sessionStorage from the storage-state JSON before the first navigation,
    # so eBay sees a warm authenticated session rather than a fresh browser.
    state_path = Path(config.EBAY_STATE_PATH)
    if not state_path.exists():
        return ListingResult(
            platform="eBay",
            success=False,
            error=(
                f"No saved session found at {state_path}. "
                "Run: python generate_cookies.py ebay"
            ),
        )
    print(f"[ebay] Loading session cookies from {state_path}")

    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-features=IsolateOrigins,site-per-process",
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
                # Step 1 — land on the sell page
                await _navigate_to_sell(page)

                # Step 2 — select category (or skip if eBay does it for us)
                await _select_category(page, item_name)

                # Step 2b — search catalog for auto-fill; fall back to skip if no match
                await _search_and_select_catalog(page, item_name)

                # Step 2c — handle Graded/Ungraded interstitial if it appears
                await _handle_graded_interstitial(page, condition)

                # Step 2d — handle specific-condition interstitial if it appears
                await _handle_specific_condition(page, condition)

                # Step 3 — fill the listing form
                await _fill_title(page, item_name)
                await page.wait_for_timeout(random.uniform(400, 800))

                await _upload_photos(page, image_paths)
                await _set_condition(page, condition)
                await _fill_description(page, description)
                await _set_price(page, price_gbp)

                await page.wait_for_timeout(random.uniform(600, 1_000))

                # Step 4 — submit
                listing_url = await _submit_and_get_url(page, dry_run)

            except (PermissionError, RuntimeError) as exc:
                await _screenshot(page, "fatal_error")
                raise exc
            finally:
                await context.close()
                await browser.close()

        return ListingResult(
            platform="eBay",
            success=True,
            listing_url=listing_url,
        )

    except PermissionError as exc:
        return ListingResult(platform="eBay", success=False, error=str(exc))
    except Exception as exc:
        return ListingResult(
            platform="eBay",
            success=False,
            error=f"{type(exc).__name__}: {exc}",
        )
