"""
PriceCharting + eBay UK scraper.

Architecture
------------
scrape_pricecharting()  — async. Tries requests+bs4 in a thread first (fast),
                          then automatically falls back to async Playwright if
                          the response is blocked or the price element is absent.

scrape_ebay_uk_sold()   — async, Playwright-primary. eBay UK aggressively blocks
                          plain requests, so we go straight to a headless browser
                          with realistic browser context settings.

get_usd_to_gbp()        — sync. Lightweight Frankfurter JSON call with a 1-hour
                          in-process cache. Call via asyncio.to_thread() from
                          async code so it doesn't stall the event loop.

ebay_clean_query()      — public helper. Strips special characters and trims to
                          6 words. Used by bot.py when no custom query is given.

Playwright browser hygiene
--------------------------
Every Playwright function opens a fresh browser context (not just a new page)
and closes it in a try/finally. This ensures cookies, storage, and network
state never leak between calls, and the browser process exits cleanly even if
scraping raises an exception.

PriceCharting price container IDs  (the six that exist on public pages)
  used_price        → Loose / Ungraded
  complete_price    → Complete-in-box
  new_price         → New / Sealed
  graded_price      → Grade 9 (repurposed by PriceCharting)
  box_only_price    → Grade 9.5 (repurposed)
  manual_only_price → PSA 10 (repurposed)

Per-grade prices for Grade 1–8 have no dedicated container IDs.
They live in <div id="full-prices"> and are found by row label text.
"""

import asyncio
import re
import time
import urllib.parse
from typing import Optional, Tuple

import requests
from bs4 import BeautifulSoup

import config

# ---------------------------------------------------------------------------
# Shared browser identity
# ---------------------------------------------------------------------------

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_REQUEST_HEADERS = {
    "User-Agent":              _UA,
    "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language":         "en-GB,en-US;q=0.7,en;q=0.3",
    "Accept-Encoding":         "gzip, deflate, br",
    "Connection":              "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "DNT":                     "1",
    # Sec-Fetch-* headers are sent by real Chrome navigations; their absence
    # is a reliable bot signal that many sites check.
    "Sec-Fetch-Dest":          "document",
    "Sec-Fetch-Mode":          "navigate",
    "Sec-Fetch-Site":          "none",
    "Sec-Fetch-User":          "?1",
    "Referer":                 "https://www.pricecharting.com/",
}

# Chromium launch flags used by both scrapers
_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    # Hides the navigator.webdriver property that sites use to detect automation
    "--disable-blink-features=AutomationControlled",
]

# ---------------------------------------------------------------------------
# Optional callbacks — registered by bot.py to track health stats
# ---------------------------------------------------------------------------

_on_429_callback: Optional[callable] = None
_on_success_callback: Optional[callable] = None

# Adaptive rate-limit state
_pc_429_streak: int   = 0
_pc_base_delay: float = 2.0
_PC_MAX_DELAY:  float = 120.0
_PC_429_ABORT_THRESHOLD: int = 8


def register_callbacks(on_429=None, on_success=None) -> None:
    """Register callables that fire on 429 errors and successful price scrapes."""
    global _on_429_callback, _on_success_callback
    _on_429_callback = on_429
    _on_success_callback = on_success


def _on_pc_429() -> None:
    global _pc_429_streak, _pc_base_delay
    _pc_429_streak += 1
    _pc_base_delay  = min(_pc_base_delay * 2, _PC_MAX_DELAY)
    if _on_429_callback:
        _on_429_callback()


def _on_pc_success() -> None:
    global _pc_429_streak, _pc_base_delay
    _pc_429_streak = 0
    _pc_base_delay = max(_pc_base_delay * 0.9, 2.0)
    if _on_success_callback:
        _on_success_callback()


def get_pc_429_streak() -> int:
    """Return current consecutive 429 count — checked by bot.py to abort the price loop."""
    return _pc_429_streak

# Maps bot dropdown strings (and common aliases) to their primary container ID.
# For PSA 1–8 there is no dedicated container; graded_price is the fallback.
CONDITION_TO_CONTAINER: dict[str, str] = {
    # Ungraded conditions
    "Near mint or better": "used_price",
    "Lightly played":      "used_price",
    "Moderately played":   "used_price",
    "Heavily played":      "used_price",
    # PSA grades — Grade 9 / 9.5 / 10 have real container IDs; 1–8 fall back to graded_price
    "PSA 10":  "manual_only_price",
    "PSA 9.5": "box_only_price",
    "PSA 9":   "graded_price",
    "PSA 8":   "graded_price",
    "PSA 7":   "graded_price",
    "PSA 6":   "graded_price",
    "PSA 5":   "graded_price",
    "PSA 4":   "graded_price",
    "PSA 3":   "graded_price",
    "PSA 2":   "graded_price",
    "PSA 1":   "graded_price",
    # BGS — Grade 9/9.5/10 have dedicated PriceCharting rows; lower fall back to graded_price
    "BGS 10":  "manual_only_price",
    "BGS 9.5": "box_only_price",
    "BGS 9":   "graded_price",
    "BGS 8.5": "graded_price",
    "BGS 8":   "graded_price",
    # CGC
    "CGC 10":  "manual_only_price",
    "CGC 9.5": "box_only_price",
    "CGC 9":   "graded_price",
    "CGC 8.5": "graded_price",
    "CGC 8":   "graded_price",
    # SGC
    "SGC 10":  "manual_only_price",
    "SGC 9.5": "box_only_price",
    "SGC 9":   "graded_price",
    "SGC 8":   "graded_price",
    # ACE has no dedicated PriceCharting row; all fall back to graded_price
    "ACE 10":  "graded_price",
    "ACE 9.5": "graded_price",
    "ACE 9":   "graded_price",
    "ACE 8":   "graded_price",
    # GetGraded has no dedicated PriceCharting row; all fall back to graded_price
    "GetGraded 10":  "graded_price",
    "GetGraded 9.5": "graded_price",
    "GetGraded 9":   "graded_price",
    "GetGraded 8":   "graded_price",
    # Legacy / generic keys kept for backward compatibility
    "ungraded":  "used_price",
    "raw":       "used_price",
    "loose":     "used_price",
    "complete":  "complete_price",
    "new":       "new_price",
    "sealed":    "new_price",
    "graded":    "graded_price",
    "box":       "box_only_price",
    "manual":    "manual_only_price",
}

# Maps graded condition strings to the row label in the <div id="full-prices"> table.
# PSA/BGS/CGC/SGC 10 have dedicated rows; grades below 10 share generic "Grade N" rows.
# BGS 10 / CGC 10 / SGC 10 have their own dedicated rows (added by PriceCharting June 2024).
# ACE has no dedicated row — using nearest generic grade as proxy.
CONDITION_TABLE_LABEL: dict[str, str] = {
    # PSA
    "PSA 10":  "PSA 10",
    "PSA 9.5": "Grade 9.5",
    "PSA 9":   "Grade 9",
    "PSA 8":   "Grade 8",
    "PSA 7":   "Grade 7",
    "PSA 6":   "Grade 6",
    "PSA 5":   "Grade 5",
    "PSA 4":   "Grade 4",
    "PSA 3":   "Grade 3",
    "PSA 2":   "Grade 2",
    "PSA 1":   "Grade 1",
    # BGS
    "BGS 10":  "BGS 10",
    "BGS 9.5": "Grade 9.5",
    "BGS 9":   "Grade 9",
    "BGS 8.5": "Grade 9",    # no Grade 8.5 row; Grade 9 is the closest available
    "BGS 8":   "Grade 8",
    # CGC
    "CGC 10":  "CGC 10",
    "CGC 9.5": "Grade 9.5",
    "CGC 9":   "Grade 9",
    "CGC 8.5": "Grade 9",    # no Grade 8.5 row; Grade 9 is the closest available
    "CGC 8":   "Grade 8",
    # SGC
    "SGC 10":  "SGC 10",
    "SGC 9.5": "Grade 9.5",
    "SGC 9":   "Grade 9",
    "SGC 8":   "Grade 8",
    # ACE has no dedicated row on PriceCharting — using nearest generic grade as proxy.
    "ACE 10":  "Grade 9.5",
    "ACE 9.5": "Grade 9.5",
    "ACE 9":   "Grade 9",
    "ACE 8":   "Grade 8",
    # GetGraded has no dedicated row on PriceCharting — using nearest generic grade as proxy.
    "GetGraded 10":  "Grade 9.5",
    "GetGraded 9.5": "Grade 9.5",
    "GetGraded 9":   "Grade 9",
    "GetGraded 8":   "Grade 8",
}


# ---------------------------------------------------------------------------
# Shared price-extraction helpers
# ---------------------------------------------------------------------------

def _parse_price(text: str) -> Optional[float]:
    """Strip currency symbols / whitespace and return a float, or None."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.,]", "", text.strip()).replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _price_from_container(container) -> Optional[float]:
    """
    Try three strategies to pull a price float from a BS4 tag:
      1. <span class="price"> or <span class="js-price"> text
      2. data-price attribute (PriceCharting stores value in cents here)
      3. First numeric-looking text anywhere inside the container
    """
    if container is None:
        return None

    for span in container.find_all("span"):
        cls = span.get("class", [])
        if "price" in cls or "js-price" in cls:
            price = _parse_price(span.get_text(strip=True))
            if price is not None:
                return price

    for tag in container.find_all(attrs={"data-price": True}):
        raw = tag["data-price"].strip()
        try:
            cents = int(raw)
            if cents > 0:
                return round(cents / 100, 2)
        except ValueError:
            price = _parse_price(raw)
            if price is not None:
                return price

    return _parse_price(container.get_text(separator=" ", strip=True))


def _price_from_full_table(soup: BeautifulSoup, label: str, _path: str = "unknown") -> Optional[float]:
    """
    Find a row in the <div id="full-prices"> table whose first cell matches
    *label* (case-insensitive) and return the price from the second cell.
    Returns None if the table or the row is absent.
    """
    fp = soup.find(id="full-prices")
    if fp is None:
        print(f"[scraper/debug] full-prices div not found (path={_path})")
        return None
    for tr in fp.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) >= 2 and tds[0].get_text(strip=True).lower() == label.lower():
            price = _price_from_container(tds[1])
            print(f"[scraper/debug] full-prices lookup label={label!r} path={_path} → price={price}")
            return price
    print(f"[scraper/debug] full-prices lookup label={label!r} path={_path} → row not found")
    return None


def _extract_price_with_fallback(
    soup: BeautifulSoup,
    condition: str,
    _path: str = "unknown",
) -> tuple[Optional[float], str]:
    """
    Three-tier price lookup for a given condition string:

    PSA grades
      1. full-prices table row for the exact grade label
      2. primary container ID from CONDITION_TO_CONTAINER
      3. used_price (last resort, prints a warning)

    Everything else
      Direct container lookup via CONDITION_TO_CONTAINER, falling back to used_price.

    Returns (price_usd, source_description).
    """
    table_label = CONDITION_TABLE_LABEL.get(condition)

    if table_label is not None:
        price = _price_from_full_table(soup, table_label, _path=_path)
        if price is not None:
            return price, f"full-prices table '{table_label}'"

        container_id = CONDITION_TO_CONTAINER.get(condition, "graded_price")
        price = _price_from_container(soup.find(id=container_id))
        if price is not None:
            print(f"[scraper/debug] price resolved via container={container_id!r} path={_path}")
            return price, f"{container_id} (grade row was empty)"

        price = _price_from_container(soup.find(id="used_price"))
        if price is None:
            return None, "no price found"
        print(f"[scraper] Warning: no graded price for '{condition}' — using used_price as fallback")
        print(f"[scraper/debug] price resolved via container='used_price' path={_path}")
        return price, "used_price (no graded price available)"

    container_id = CONDITION_TO_CONTAINER.get(condition) or CONDITION_TO_CONTAINER.get(
        condition.lower(), "used_price"
    )
    price = _price_from_container(soup.find(id=container_id))
    if price is not None:
        print(f"[scraper/debug] price resolved via container={container_id!r} path={_path}")
        return price, container_id

    price = _price_from_container(soup.find(id="used_price"))
    if price is not None:
        print(f"[scraper/debug] price resolved via container='used_price' path={_path} (fallback)")
    return price, "used_price (container fallback)"


def _clean_card_name(raw_name: str) -> str:
    """URL-decode percent-encoded characters in a card name (%27 -> ', %26 -> &, etc.)
    and fix str.title() apostrophe quirk ("Ethan'S" -> "Ethan's")."""
    import re as _re
    if not raw_name:
        return raw_name
    decoded = urllib.parse.unquote(raw_name).strip()
    # str.title() treats apostrophes as word separators producing "Ethan'S".
    # Lowercase the letter immediately after an apostrophe that follows a word char.
    return _re.sub(r"(?<=\w)'([A-Z])", lambda m: "'" + m.group(1).lower(), decoded)


def _name_from_url(url: str) -> str:
    """
    Derive a readable card name from a PriceCharting URL as a last resort.

    For .../game/pokemon-ascended-heroes/mega-dragonite-ex-271 this returns
    "Mega Dragonite Ex 271 (Pokemon Ascended Heroes)".
    Never returns an empty string — falls back to the raw URL if parsing fails.
    """
    try:
        path  = urllib.parse.urlparse(url).path
        # URL-decode each path segment before slug manipulation so %27 -> '
        parts = [urllib.parse.unquote(p) for p in path.strip("/").split("/") if p]
        import string as _string
        # Expected structure: ["game", "<console-slug>", "<card-slug>"]
        if len(parts) >= 3 and parts[0] == "game":
            card    = _string.capwords(parts[2].replace("-", " "))
            console = _string.capwords(parts[1].replace("-", " "))
            return f"{card} ({console})"
        if parts:
            return _string.capwords(parts[-1].replace("-", " "))
    except Exception:
        pass
    return url


def _soup_extract(soup: BeautifulSoup, condition: str, url: str = "", _path: str = "unknown") -> Tuple[str, Optional[float]]:
    """
    Parse item name and price from an already-fetched BeautifulSoup object.

    Name resolution order:
      1. <h1 id="product_name">  — PriceCharting's canonical element
      2. Any element with id="product_name"
      3. The first <h1> on the page
      4. URL-derived name via _name_from_url (when url is supplied)
      5. "Unknown Item" (absolute last resort)

    Price is resolved via _extract_price_with_fallback (full-prices table →
    primary container ID → used_price).
    """
    name_tag = (
        soup.find("h1", id="product_name")
        or soup.find(id="product_name")
        or soup.find("h1")
    )
    item_name = _clean_card_name(name_tag.get_text(separator=" ", strip=True)) if name_tag else ""

    if not item_name:
        item_name = _name_from_url(url) if url else "Unknown Item"

    price, _ = _extract_price_with_fallback(soup, condition, _path=_path)
    return item_name, price


# ---------------------------------------------------------------------------
# PriceCharting — public async interface
# ---------------------------------------------------------------------------

async def scrape_pricecharting(
    url: str,
    condition: str = "ungraded",
) -> Tuple[str, float]:
    """
    Scrape PriceCharting and return (item_name, estimated_value_usd).

    Tries requests+bs4 first (fast); if the response is blocked or the price
    element is missing from the HTML, falls back to Playwright automatically.

    Raises ValueError if the price cannot be obtained by either method.
    """
    # Fast path: run blocking requests in a thread so the event loop stays free
    try:
        item_name, price = await asyncio.to_thread(
            _pc_requests, url, condition
        )
        if price is not None:
            return item_name, price
        print("[scraper/pc] requests+bs4 found no price — falling back to Playwright")
    except ValueError as exc:
        print(f"[scraper/pc] requests failed ({exc}) — falling back to Playwright")

    return await _pw_pricecharting(url, condition)


def _pc_requests(url: str, condition: str) -> Tuple[str, Optional[float]]:
    """Synchronous requests scrape; intended to be called via asyncio.to_thread."""
    try:
        resp = requests.get(url, headers=_REQUEST_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 429:
            _on_pc_429()
        raise ValueError(f"PriceCharting returned HTTP {status}") from exc
    except requests.RequestException as exc:
        raise ValueError(f"Network error ({type(exc).__name__}): {exc}") from exc

    # Debug: print first 2000 chars of HTML to see what we got
    print(f"[scraper/_pc_requests] HTML response (first 2000 chars):\n{resp.text[:2000]}")

    # Check for price indicators in HTML
    has_complete_cost = "complete_cost" in resp.text
    has_price_class = "class=\"price" in resp.text
    has_completed_auctions = "completed_auctions" in resp.text
    print(f"[scraper/_pc_requests] HTML analysis: has_complete_cost={has_complete_cost}, has_price_class={has_price_class}, has_completed_auctions={has_completed_auctions}")

    soup = BeautifulSoup(resp.text, "lxml")

    # Debug: check for specific price elements
    complete_cost = soup.find(id="complete_cost")
    print(f"[scraper/_pc_requests] Found <span id='complete_cost'>: {complete_cost is not None}")
    if complete_cost:
        print(f"[scraper/_pc_requests] complete_cost content: {complete_cost.get_text()[:100]}")

    # Check for completed_auctions table
    auctions_table = soup.find("table", id="completed_auctions")
    print(f"[scraper/_pc_requests] Found <table id='completed_auctions'>: {auctions_table is not None}")

    return _soup_extract(soup, condition, url, _path="requests")


async def _pw_pricecharting(url: str, condition: str) -> Tuple[str, float]:
    """Async Playwright scrape of PriceCharting with stealth settings."""
    _require_playwright()
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
        context = await browser.new_context(
            user_agent=_UA,
            locale="en-GB",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept-Language":         "en-GB,en-US;q=0.7,en;q=0.3",
                "Upgrade-Insecure-Requests": "1",
                "DNT":                     "1",
            },
        )
        # Remove the webdriver flag that sites inspect via JS
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            try:
                # Wait for actual price data — [data-price] is only present once JS
                # has populated the price containers. Waiting for the container div
                # itself (#used_price) is not sufficient because it appears empty
                # before prices load. Also wait for full-prices table rows as a
                # secondary signal for graded prices.
                await page.wait_for_selector(
                    "[data-price], #full-prices td, #used_price .price, #used_price .js-price",
                    timeout=15_000,
                )
            except PWTimeout:
                pass  # Take whatever loaded
            html = await page.content()
        finally:
            await context.close()
            await browser.close()

    item_name, price = _soup_extract(BeautifulSoup(html, "lxml"), condition, url, _path="playwright")

    if price is None:
        raise ValueError(
            f"Could not find a price for condition '{condition}' even after Playwright. "
            "The condition may not be listed for this item — try a different condition."
        )

    return item_name, price


# ---------------------------------------------------------------------------
# Currency conversion  (Frankfurter — no API key, free, updated daily)
# ---------------------------------------------------------------------------

_fx_cache: dict = {"rate": None, "ts": 0.0}
_FX_TTL = 3600.0


def get_usd_to_gbp() -> float:
    """
    Return the USD → GBP exchange rate via Frankfurter. Cached for one hour.

    Synchronous — always call via asyncio.to_thread() from async code.
    Falls back to the last successful rate if the network refresh fails.
    """
    now = time.monotonic()
    if _fx_cache["rate"] is not None and now - _fx_cache["ts"] < _FX_TTL:
        return _fx_cache["rate"]

    try:
        resp = requests.get(
            "https://api.frankfurter.app/latest",
            params={"from": "USD", "to": "GBP"},
            timeout=10,
        )
        resp.raise_for_status()
        rate = float(resp.json()["rates"]["GBP"])
        _fx_cache.update({"rate": rate, "ts": now})
        return rate
    except Exception as exc:
        if _fx_cache["rate"] is not None:
            print(f"[scraper/fx] Refresh failed ({exc}); using cached rate.")
            return _fx_cache["rate"]
        raise ValueError(f"Could not fetch USD→GBP rate: {exc}") from exc


# ---------------------------------------------------------------------------
# eBay UK — sold / completed listings (Playwright-primary)
# ---------------------------------------------------------------------------

_EBAY_URL = "https://www.ebay.co.uk/sch/i.html"

# eBay UK GDPR consent overlay selectors — tried in order, first hit wins
_GDPR_SELECTORS = (
    "button#gdpr-banner-accept",
    "button[name='accept']",
    "button:has-text('Accept all')",
    "button:has-text('I Accept')",
    "button:has-text('Accept All')",
)


def ebay_clean_query(name: str) -> str:
    """
    Strip special characters from an item name and keep the first 6 words.

    This prevents over-specific searches (card set numbers, hyphens, etc.)
    that return zero results on eBay.

    Examples
    --------
    "Mega Dragonite ex #271Pokemon Ascended Heroes" → "Mega Dragonite ex 271Pokemon Ascended Heroes"
    (truncated) → "Mega Dragonite ex 271Pokemon Ascended"
    """
    cleaned = re.sub(r"[^\w\s]", " ", name)
    return " ".join(cleaned.split()[:6])


async def scrape_ebay_uk_sold(
    search_query: str,
    max_results: int = 5,
) -> tuple[Optional[float], int]:
    """
    Search eBay UK completed/sold listings via Playwright and return
    (avg_price_gbp, count) for the most recent ``max_results`` sales.

    Parameters
    ----------
    search_query : Exact string submitted to eBay — should already be cleaned
                   or set to the user's custom override (bot.py handles this).
    max_results  : Cap on how many sold prices to include in the average.
                   If fewer are available, those are averaged instead.

    Returns
    -------
    (avg_price_gbp, count)  where count reflects how many listings were used.
    Returns (None, 0) if no sold listings were found or parsed.
    """
    return await _pw_ebay_uk(search_query, max_results)


async def _pw_ebay_uk(search_query: str, max_results: int) -> tuple[Optional[float], int]:
    """
    Core async Playwright implementation for eBay UK sold listings.

    Stealth measures applied:
      - navigator.webdriver removed via init script
      - en-GB locale + Europe/London timezone (matches ebay.co.uk)
      - Realistic viewport
      - GDPR consent dialog auto-dismissed
      - --disable-blink-features=AutomationControlled launch flag
    """
    _require_playwright()
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    qs = urllib.parse.urlencode({
        "_nkw":        search_query,
        "LH_Sold":     "1",
        "LH_Complete": "1",
        "_sop":        "13",  # most recently ended first
        "_ipg":        "60",  # more listings to sample from
    })
    url = f"{_EBAY_URL}?{qs}"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
        context = await browser.new_context(
            user_agent=_UA,
            locale="en-GB",
            timezone_id="Europe/London",
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept-Language":         "en-GB,en;q=0.9",
                "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Upgrade-Insecure-Requests": "1",
                "DNT":                     "1",
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = await context.new_page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            # Dismiss eBay's GDPR consent overlay if it appears
            for sel in _GDPR_SELECTORS:
                try:
                    await page.locator(sel).first.click(timeout=2_500)
                    await page.wait_for_timeout(300)
                    break
                except PWTimeout:
                    continue

            # Wait for listing cards — if none arrive we'll try parsing anyway
            try:
                await page.wait_for_selector("li.s-item", timeout=15_000)
            except PWTimeout:
                pass

            html = await page.content()
        finally:
            await context.close()
            await browser.close()

    prices = _ebay_extract_prices(BeautifulSoup(html, "lxml"), max_results)

    if not prices:
        return None, 0

    return round(sum(prices) / len(prices), 2), len(prices)


def _ebay_extract_prices(soup: BeautifulSoup, limit: int) -> list[float]:
    """
    Pull sold prices from an eBay UK search-results page.

    CSS selectors tried per listing (in priority order):
      1. span.s-item__price          — current eBay layout
      2. .s-item__detail span.BOLD   — older eBay layout
      3. span[class*='price']        — broad fallback
    """
    prices: list[float] = []

    for item in soup.select("li.s-item"):
        if len(prices) >= limit:
            break

        # eBay injects a phantom first <li> with title "Shop on eBay" — skip it
        title_el = item.select_one(".s-item__title")
        if title_el and "shop on ebay" in title_el.get_text().lower():
            continue

        price_text: Optional[str] = None
        for selector in (
            "span.s-item__price",
            ".s-item__detail span.BOLD",
            "span[class*='price']",
        ):
            tag = item.select_one(selector)
            if tag:
                price_text = tag.get_text(strip=True)
                break

        if not price_text:
            continue

        # "£5.00 to £10.00" price range — take the lower bound
        if " to " in price_text.lower():
            price_text = price_text.lower().split(" to ")[0].strip()

        price = _parse_price(price_text)
        if price is not None and price > 0:
            prices.append(price)

    return prices


# ---------------------------------------------------------------------------
# Condition → PriceCharting container mapping
# ---------------------------------------------------------------------------

def condition_to_container(condition: str) -> str:
    """
    Return the primary PriceCharting container ID for a given condition string.

    Exact matches against CONDITION_TO_CONTAINER are tried first (covers all
    bot dropdown values).  Falls back to a lower-cased lookup, then "used_price".
    For PSA grades the full-prices table is the primary source; the container
    returned here is the secondary fallback used when the table row is empty.
    """
    return (
        CONDITION_TO_CONTAINER.get(condition)
        or CONDITION_TO_CONTAINER.get(condition.lower().strip(), "used_price")
    )


# ---------------------------------------------------------------------------
# Convenience wrapper used by /add and the price-update loop
# ---------------------------------------------------------------------------

async def scrape_card(
    url: str,
    condition: str = "Near mint or better",
    region: str = "",
) -> tuple[str, Optional[float]]:
    """
    Scrape a PriceCharting URL and return (card_name, live_price_gbp).

    condition : Bot condition string (e.g. "Near mint or better", "PSA 10").
                Per-grade prices are fetched from the full-prices table when
                available, with graded_price and used_price as fallbacks.
    region    : "" (standard), "JP", or "KR". When "KR", the GBP price is
                multiplied by config.KOREAN_PRICE_MULTIPLIER before returning,
                since PriceCharting only carries the Japanese-print price.

    live_price_gbp is None if PriceCharting has no price listed for that condition.
    Never raises; logs failures and degrades gracefully.
    """
    card_name: str             = ""
    usd_price: Optional[float] = None

    # Fast path: requests + bs4 (URL passed through so _soup_extract can use it)
    try:
        card_name, usd_price = await asyncio.to_thread(_pc_requests, url, condition)
        print(f"[scraper/card] HTTP scrape successful: card_name={card_name}, price=${usd_price}")
    except ValueError as exc:
        print(f"[scraper/card] HTTP scrape failed: {exc}")

    # No Playwright fallback - if HTTP fails, derive name from URL and return None price
    if not card_name:
        card_name = _name_from_url(url)
        print(f"[scraper/card] Derived card_name from URL: {card_name}")

    if usd_price is None:
        print(f"[scraper/card] No price found (HTTP only, no Playwright fallback)")
        return card_name, None

    try:
        fx_rate = await asyncio.to_thread(get_usd_to_gbp)
        live_price_gbp = round(usd_price * fx_rate, 2)
    except Exception as exc:
        print(f"[scraper/card] FX conversion failed ({exc}) — returning raw USD price")
        live_price_gbp = round(usd_price, 2)

    if region == "KR":
        live_price_gbp = round(live_price_gbp * config.KOREAN_PRICE_MULTIPLIER, 2)
        print(
            f"[scraper/card] Applied KR multiplier "
            f"({config.KOREAN_PRICE_MULTIPLIER}) → £{live_price_gbp}"
        )

    _on_pc_success()

    return card_name, live_price_gbp


async def scrape_card_http_only(
    url: str,
    condition: str = "Near mint or better",
    region: str = "",
) -> tuple[str, Optional[float]]:
    """
    Scrape a PriceCharting URL using HTTP only (no Playwright fallback).
    Used by /add endpoint for fast, lightweight scraping.

    Returns (card_name, live_price_gbp) or (card_name_from_url, None) if no price found.
    Never raises; logs failures and degrades gracefully.
    """
    card_name: str             = ""
    usd_price: Optional[float] = None

    # HTTP-only path: requests + bs4 (no Playwright fallback)
    try:
        card_name, usd_price = await asyncio.to_thread(_pc_requests, url, condition)
        print(f"[scraper/card_http] HTTP scrape: card_name={card_name}, price={usd_price}")
    except ValueError as exc:
        print(f"[scraper/card_http] HTTP scrape failed ({exc})")

    # Fallback: derive name from URL if HTTP scraper failed to get name
    if not card_name:
        card_name = _name_from_url(url)

    # Return early if no price found (no Playwright fallback)
    if usd_price is None:
        print(f"[scraper/card_http] No price found, returning card_name={card_name}")
        return card_name, None

    # Convert USD to GBP
    try:
        fx_rate = await asyncio.to_thread(get_usd_to_gbp)
        live_price_gbp = round(usd_price * fx_rate, 2)
    except Exception as exc:
        print(f"[scraper/card_http] FX conversion failed ({exc}) — returning raw USD price")
        live_price_gbp = round(usd_price, 2)

    # Apply region multiplier if needed
    if region == "KR":
        live_price_gbp = round(live_price_gbp * config.KOREAN_PRICE_MULTIPLIER, 2)
        print(f"[scraper/card_http] Applied KR multiplier → £{live_price_gbp}")

    _on_pc_success()

    return card_name, live_price_gbp


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Shared Playwright page-fetch helper (used by image scraper and price scraper)
# ---------------------------------------------------------------------------

_html_semaphore = asyncio.Semaphore(3)  # max 3 concurrent browser instances


async def fetch_page_html(url: str, *, wait_selector: str | None = None, timeout_ms: int = 20_000) -> str | None:
    """
    Fetch a page via headless Chromium with the same stealth settings used for
    PriceCharting price scraping.  Returns the full HTML string, or None on any
    failure.  At most 3 instances run concurrently (shared with image scraping).
    """
    _require_playwright()
    from playwright.async_api import async_playwright, TimeoutError as PWTimeout

    async with _html_semaphore:
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True, args=_BROWSER_ARGS)
                context = await browser.new_context(
                    user_agent=_UA,
                    locale="en-GB",
                    viewport={"width": 1280, "height": 800},
                    extra_http_headers={
                        "Accept-Language":           "en-GB,en-US;q=0.7,en;q=0.3",
                        "Upgrade-Insecure-Requests": "1",
                        "DNT":                       "1",
                    },
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
                )
                page = await context.new_page()

                # Block fonts, ad networks, and analytics to keep load fast
                async def _abort_unnecessary(route):
                    u = route.request.url
                    if any(p in u for p in ("analytics", "/ads/", "doubleclick", "googletag",
                                             ".woff", ".woff2", ".ttf", ".otf")):
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", _abort_unnecessary)

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                    if wait_selector:
                        try:
                            await page.wait_for_selector(wait_selector, timeout=10_000)
                        except PWTimeout:
                            pass
                    html = await page.content()
                finally:
                    await context.close()
                    await browser.close()

            return html
        except Exception as exc:
            print(f"[fetch_page_html] Error fetching {url}: {exc}")
            return None


# ---------------------------------------------------------------------------
# Internal guard
# ---------------------------------------------------------------------------

def _require_playwright() -> None:
    """Raise a clear ImportError if playwright is not installed."""
    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "playwright is not installed. Run:\n"
            "  pip install playwright && playwright install chromium"
        ) from exc


if __name__ == "__main__":
    # --- Static mapping tests ---
    assert condition_to_container("PSA 10")              == "manual_only_price"
    assert condition_to_container("PSA 9.5")             == "box_only_price"
    assert condition_to_container("PSA 9")               == "graded_price"
    assert condition_to_container("PSA 1")               == "graded_price"
    assert condition_to_container("Near mint or better") == "used_price"
    assert condition_to_container("Heavily played")      == "used_price"
    assert condition_to_container("unknown_value")       == "used_price"
    assert CONDITION_TABLE_LABEL["PSA 10"]  == "PSA 10"
    assert CONDITION_TABLE_LABEL["PSA 9.5"] == "Grade 9.5"
    assert CONDITION_TABLE_LABEL["PSA 9"]   == "Grade 9"
    assert CONDITION_TABLE_LABEL["PSA 1"]   == "Grade 1"
    print("Static mapping tests: all passed.")

    # --- Unit tests for extraction helpers (no network required) ---
    _FAKE_HTML = """
    <html><body>
      <h1 id="product_name">Test Card</h1>
      <div id="used_price"><span class="price">$10.00</span></div>
      <div id="graded_price"><span class="price">$50.00</span></div>
      <div id="manual_only_price"><span class="price">$200.00</span></div>
      <div id="full-prices">
        <table>
          <tr><td>Grade 1</td><td><span class="price">$20.00</span></td></tr>
          <tr><td>Grade 9</td><td><span class="price">$80.00</span></td></tr>
          <tr><td>Grade 9.5</td><td><span class="price">$150.00</span></td></tr>
          <tr><td>PSA 10</td><td><span class="price">$250.00</span></td></tr>
        </table>
      </div>
    </body></html>
    """
    _fake_soup = BeautifulSoup(_FAKE_HTML, "lxml")

    # Ungraded: should use used_price container
    p, src = _extract_price_with_fallback(_fake_soup, "Near mint or better")
    assert p == 10.0, f"Expected 10.0 got {p}"

    # PSA 1: full-prices table has "Grade 1" row → $20
    p, src = _extract_price_with_fallback(_fake_soup, "PSA 1")
    assert p == 20.0, f"Expected 20.0 got {p}"
    assert "full-prices" in src

    # PSA 9: full-prices table has "Grade 9" row → $80 (beats graded_price $50)
    p, src = _extract_price_with_fallback(_fake_soup, "PSA 9")
    assert p == 80.0, f"Expected 80.0 got {p}"
    assert "full-prices" in src

    # PSA 9.5: full-prices table has "Grade 9.5" row → $150 (beats box_only_price)
    p, src = _extract_price_with_fallback(_fake_soup, "PSA 9.5")
    assert p == 150.0, f"Expected 150.0 got {p}"
    assert "full-prices" in src

    # PSA 10: full-prices table has "PSA 10" row → $250 (beats manual_only_price $200)
    p, src = _extract_price_with_fallback(_fake_soup, "PSA 10")
    assert p == 250.0, f"Expected 250.0 got {p}"
    assert "full-prices" in src

    # PSA 8: not in full-prices table → falls back to graded_price $50
    p, src = _extract_price_with_fallback(_fake_soup, "PSA 8")
    assert p == 50.0, f"Expected 50.0 got {p}"
    assert "graded_price" in src

    print("Extraction unit tests: all passed.")

    # --- Live scrape test (requires Playwright + network) ---
    import asyncio as _asyncio

    _URL = "https://www.pricecharting.com/game/pokemon-ascended-heroes/mega-dragonite-ex-271"

    async def _live_test():
        results = {}
        for cond in ("Near mint or better", "PSA 9", "PSA 9.5", "PSA 10"):
            name, gbp = await scrape_card(_URL, cond)
            results[cond] = gbp
            print(f"  {cond:25s}  name={name!r}  gbp={gbp}")
        # All four should be distinct (ungraded ≈ £50, PSA 9 ≈ £90, PSA 9.5 ≈ £220, PSA 10 ≈ £300+)
        assert results["Near mint or better"] != results["PSA 9"],   "Ungraded and PSA 9 prices should differ"
        assert results["PSA 9"]               != results["PSA 9.5"], "PSA 9 and PSA 9.5 prices should differ"
        assert results["PSA 9.5"]             != results["PSA 10"],  "PSA 9.5 and PSA 10 prices should differ"
        print("Live scrape test: all assertions passed.")

    try:
        import playwright  # noqa: F401
        _asyncio.run(_live_test())
    except ImportError:
        print("Live scrape test: skipped (playwright not installed).")
