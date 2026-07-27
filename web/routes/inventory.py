import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

import audit
import lister_ebay_api
import scraper
from web import db_inventory as db
from web import user_config
from web.auth import get_current_user

router = APIRouter()

# ── Image cache (in-memory + file-backed) ─────────────────────────────────
# Format: {"123_<user_id>": {"url": "https://...", "ts": 1234567890}}
# Keyed by (item_id, user_id) because item_id is only unique per-user, not globally.
# None results are stored as {"url": null, "ts": timestamp} and retried after 24h.
_CACHE_FILE = Path(__file__).parent.parent / "image_cache.json"
_image_cache: dict[str, dict] = {}
_cache_lock = asyncio.Lock()
_NONE_TTL = 86400  # retry failed scrapes after 24 hours


def _load_cache() -> None:
    global _image_cache
    if _CACHE_FILE.exists():
        try:
            raw = json.loads(_CACHE_FILE.read_text())
            migrated: dict[str, dict] = {}
            dropped = 0
            for k, v in raw.items():
                if isinstance(v, dict):
                    # Drop None entries so they retry with Playwright
                    if v.get("url") is None:
                        dropped += 1
                        continue
                    migrated[k] = v
                elif v:
                    # Migrate old flat string format
                    migrated[k] = {"url": v, "ts": 0}
                else:
                    dropped += 1  # old None result
            _image_cache = migrated
            print(f"[image_cache] Loaded {len(_image_cache)} entries, dropped {dropped} None results")
        except Exception:
            _image_cache = {}


def _save_cache() -> None:
    try:
        _CACHE_FILE.write_text(json.dumps(_image_cache, indent=2))
    except Exception:
        pass


def _read_cache(cache_key: str) -> str | None:
    """Return url string, None (confirmed no image), or raise KeyError (cache miss)."""
    entry = _image_cache.get(cache_key)
    if entry is None:
        raise KeyError(cache_key)
    if entry.get("url") is None:
        if time.time() - entry.get("ts", 0) > _NONE_TTL:
            raise KeyError(cache_key)  # expired — treat as miss, allow retry
        return None
    url = entry["url"]
    # Upgrade to higher resolution on read
    return _upgrade_image_url(url) if url else url


def _write_cache(cache_key: str, url: Optional[str]) -> None:
    _image_cache[cache_key] = {"url": url, "ts": int(time.time())}
    _save_cache()


_load_cache()

# Ordered by specificity — most likely first, broad fallbacks last.
_IMG_CSS_SELECTORS = [
    "img#product-image",
    "#product-image img",
    ".product-image img",
    "img.card-image",
    "#GameImage img",
    ".game-image img",
    "img[itemprop='image']",
    "img[src*='pricecharting']",
    "img[src*='gamevaluenow']",
    "main img",
    "#main-content img",
    ".main img",
    "article img",
]

_IMG_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _upgrade_image_url(url: str) -> str:
    """Upgrade TCGPlayer CDN image URLs to higher resolution versions."""
    if not url:
        return url
    # TCGPlayer CDN size upgrade
    url = url.replace('fit-in/64x89/', 'fit-in/400x557/')
    url = url.replace('fit-in/128x178/', 'fit-in/400x557/')
    url = url.replace('fit-in/146x204/', 'fit-in/400x557/')
    url = url.replace('fit-in/200x279/', 'fit-in/400x557/')
    url = url.replace('fit-in/300x417/', 'fit-in/400x557/')
    return url


def _resolve_src(src: str) -> Optional[str]:
    """Normalise a relative/protocol-relative src to an absolute https URL."""
    src = src.strip()
    if not src:
        return None
    if src.startswith("//"):
        src = "https:" + src
    elif src.startswith("/"):
        src = "https://www.pricecharting.com" + src
    if not src.startswith("http"):
        return None
    if not any(ext in src.lower() for ext in _IMG_EXTENSIONS):
        return None
    # Upgrade to higher resolution if applicable
    src = _upgrade_image_url(src)
    return src


async def _scrape_pc_image(pc_url: str, item_id: int = 0) -> Optional[str]:
    """Fetch a PriceCharting page via HTTP and extract the card image from og:image meta tag."""
    import requests

    try:
        # Use HTTP scraper instead of Playwright
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-GB,en;q=0.5',
        }
        print(f"[image] Fetching {pc_url} via HTTP for item {item_id}")
        resp = requests.get(pc_url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"[image] HTTP fetch failed for item {item_id}: {e}")
        return None

    if not html:
        print(f"[image] Empty response for item {item_id}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    # First try: og:image meta tag (always in static HTML, most reliable)
    og_image = soup.find('meta', property='og:image')
    if og_image:
        image_url = og_image.get('content')
        if image_url:
            print(f"[image] Found og:image for item {item_id}: {image_url[:80]}")
            return image_url

    # Fallback: look for specific selectors
    for sel in _IMG_CSS_SELECTORS:
        el = soup.select_one(sel)
        if not el:
            continue
        for attr in ("src", "data-src", "data-lazy", "data-original"):
            raw = el.get(attr)
            if raw:
                resolved = _resolve_src(str(raw))
                if resolved:
                    print(f"[image] Found for item {item_id} via '{sel}': {resolved[:80]}")
                    return resolved

    # Last resort: find any reasonably-sized image that isn't a logo/icon
    for img in soup.find_all("img"):
        for attr in ("src", "data-src"):
            raw = img.get(attr)
            if not raw:
                continue
            resolved = _resolve_src(str(raw))
            if resolved and "logo" not in resolved and "icon" not in resolved:
                print(f"[image] Fallback image for item {item_id}: {resolved[:80]}")
                return resolved

    print(f"[image] No card image found for item {item_id} ({pc_url})")
    return None


def _ensure_cache_upgraded() -> None:
    """Upgrade existing cache entries to higher resolution image URLs."""
    global _image_cache
    updated = 0
    for key, entry in list(_image_cache.items()):
        if entry.get("url"):
            old_url = entry["url"]
            new_url = _upgrade_image_url(old_url)
            if new_url != old_url:
                _image_cache[key]["url"] = new_url
                updated += 1
    if updated > 0:
        _save_cache()
        print(f"[image_cache] Upgraded {updated} entries to higher resolution URLs")


_ensure_cache_upgraded()

# ── Field allowlists for PATCH endpoints ─────────────────────────────────
# Deliberately restricted — never let a request body pick an arbitrary DB
# column (e.g. user_id, item_id) via the field name.
_PATCHABLE_FIELDS = {
    "card_name", "purchase_price", "condition", "region", "pc_url",
    "sell_price", "ebay_listed", "status", "source",
}
_NUMERIC_FIELDS = {"purchase_price", "live_price", "quick_price", "potential_profit", "sell_price", "profit"}


def _cast_field_value(field: str, value):
    if field in _NUMERIC_FIELDS:
        return float(value)
    return value


# ── Routes ────────────────────────────────────────────────────────────────

@router.get("")
async def get_inventory(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user: dict = Depends(get_current_user),
):
    status_filter = status if status in ("Inventory", "Sold") else None
    items = await db.get_all_items(user["id"], status_filter=status_filter)

    # Fetch eBay listing prices (both live_price and sell_price) for items with ebay_listing_id
    # Global timeout: 10 seconds for all eBay fetches. Individual fetches timeout at 5 seconds.
    # If eBay is slow/unavailable, inventory still loads with null prices.
    try:
        # Group by listing ID first to detect bundles
        listings_by_id = {}
        for item in items:
            listing_id = item.get("ebay_listing_id")
            if listing_id and item.get("ebay_listed") == "Yes":
                if listing_id not in listings_by_id:
                    listings_by_id[listing_id] = []
                listings_by_id[listing_id].append(item)

        # Wrap entire fetch in 10-second timeout so inventory never hangs
        async def fetch_ebay_prices():
            # Fetch prices from eBay using user's credentials
            async with user_config.apply(user):
                # Fetch prices from eBay and sync to items
                for listing_id, listing_items in listings_by_id.items():
                    try:
                        # Individual fetch with 5-second timeout
                        offer = await asyncio.wait_for(
                            lister_ebay_api.get_offer_details(listing_id),
                            timeout=5.0
                        )

                        # Log for test listings
                        if listing_id in ("336711544234", "336711556909"):
                            print(f"\n{'='*80}")
                            print(f"[inventory-FETCH] TEST LISTING {listing_id}")
                            print(f"[inventory-FETCH] eBay API Response: {offer}")
                            print(f"[inventory-FETCH] Items sharing this listing: {len(listing_items)}")
                            for item in listing_items:
                                print(f"[inventory-FETCH]   - Item {item['item_id']}: {item['card_name']}")
                            print(f"{'='*80}\n")

                        if not offer:
                            print(f"[inventory] No offer found for listing {listing_id}")
                            continue

                        current_price = offer.get("current_price")
                        if not current_price:
                            print(f"[inventory] No current_price in offer for listing {listing_id}")
                            continue

                        # Detect if this is a true bundle or quantity listing
                        unique_names = set(i.get("card_name", "") for i in listing_items)
                        is_bundle = len(unique_names) > 1

                        # Determine price per item
                        if len(listing_items) > 1 and is_bundle:
                            price_per_item = round(current_price / len(listing_items), 2)
                            print(f"[inventory] Listing {listing_id}: True bundle - £{current_price} ÷ {len(listing_items)} = £{price_per_item} per item")
                        else:
                            price_per_item = current_price
                            if len(listing_items) > 1:
                                print(f"[inventory] Listing {listing_id}: Quantity listing - £{current_price} for each of {len(listing_items)} items")

                        # Update sell_price for all items sharing this listing
                        for item in listing_items:
                            item["sell_price"] = price_per_item
                            print(f"[inventory] Item {item['item_id']}: set sell_price = £{price_per_item}")

                    except asyncio.TimeoutError:
                        print(f"[inventory] Timeout fetching listing {listing_id} (>5s)")
                    except Exception as e:
                        print(f"[inventory] Error fetching listing {listing_id}: {e}")

                # Fetch eBay market prices for items with null live_price
                for item in items:
                    listing_id = item.get("ebay_listing_id")
                    if listing_id and not item.get("live_price"):
                        try:
                            # Individual fetch with 5-second timeout
                            offer = await asyncio.wait_for(
                                lister_ebay_api.get_offer_details(listing_id),
                                timeout=5.0
                            )
                            if offer and offer.get("current_price"):
                                item["live_price"] = offer["current_price"]
                                print(f"[inventory] Fetched market price for item {item['item_id']}: £{item['live_price']}")
                        except asyncio.TimeoutError:
                            print(f"[inventory] Timeout fetching market price for listing {listing_id} (>5s)")
                        except Exception as e:
                            print(f"[inventory] Failed to fetch market price for listing {listing_id}: {e}")

        # Global 10-second timeout for entire eBay fetch section
        try:
            await asyncio.wait_for(fetch_ebay_prices(), timeout=10.0)
        except asyncio.TimeoutError:
            print(f"[inventory] Warning: eBay price fetch exceeded 10s timeout, returning without prices")

    except Exception as e:
        print(f"[inventory] Warning: eBay price fetch failed, continuing without prices: {e}")

    # Split live_price for bundles (already split sell_price above)
    # Rebuild listing groups to detect true bundles
    all_listings_by_id = {}
    for item in items:
        listing_id = item.get("ebay_listing_id")
        if listing_id:
            if listing_id not in all_listings_by_id:
                all_listings_by_id[listing_id] = []
            all_listings_by_id[listing_id].append(item)

    # Check which listings are true bundles (different card names)
    bundles = {}
    for listing_id, listing_items in all_listings_by_id.items():
        if len(listing_items) > 1:
            # Get unique card names in this listing
            unique_names = set(i.get("card_name", "") for i in listing_items)
            # True bundle if card names differ, quantity listing if all same
            bundles[listing_id] = len(unique_names) > 1

    # Adjust live_price only for true bundles (not quantity listings)
    for item in items:
        listing_id = item.get("ebay_listing_id")
        if listing_id and bundles.get(listing_id, False):
            # This is a true bundle (different items) — divide price equally
            count = len(all_listings_by_id[listing_id])
            if item.get("live_price"):
                item["live_price"] = round(item["live_price"] / count, 2)

    if search:
        q = search.lower()
        items = [
            i for i in items
            if q in (i.get("card_name") or "").lower()
            or q in (i.get("condition") or "").lower()
            or q in (i.get("region") or "").lower()
        ]
    return {"items": items, "total": len(items)}


@router.get("/{item_id}/image")
async def get_card_image(item_id: int, user: dict = Depends(get_current_user)):
    cache_key = f"{item_id}_{user['id']}"
    async with _cache_lock:
        try:
            return {"image_url": _read_cache(cache_key), "cached": True}
        except KeyError:
            pass  # cache miss — fall through to scrape

    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    pc_url = item.get("pc_url") or ""
    image_url = await _scrape_pc_image(pc_url, item_id) if pc_url else None

    async with _cache_lock:
        _write_cache(cache_key, image_url)

    return {"image_url": image_url, "cached": False}


@router.delete("/{item_id}/image/cache")
async def clear_image_cache(item_id: int, user: dict = Depends(get_current_user)):
    """Force re-scrape on next request (use when PC URL changes)."""
    cache_key = f"{item_id}_{user['id']}"
    async with _cache_lock:
        existed = cache_key in _image_cache
        if existed:
            del _image_cache[cache_key]
            _save_cache()
    return {"cleared": existed}


@router.delete("/image/cache/all")
async def clear_all_image_cache(user: dict = Depends(get_current_user)):
    """Clear the entire image cache to force re-scraping with updated selectors."""
    global _image_cache
    async with _cache_lock:
        count = len(_image_cache)
        _image_cache = {}
        _save_cache()
    return {"cleared": count}


@router.get("/{item_id}")
async def get_item(item_id: int, user: dict = Depends(get_current_user)):
    try:
        item = await db.get_item(user["id"], item_id)

        # Fetch eBay listing price for bundle items with null live_price
        listing_id = item.get("ebay_listing_id")
        if listing_id and not item.get("live_price"):
            try:
                offer = await lister_ebay_api.get_offer_details(listing_id)
                if offer and offer.get("current_price"):
                    item["live_price"] = offer["current_price"]
            except Exception as e:
                print(f"[inventory] Failed to fetch price for listing {listing_id}: {e}")

        # Split listing price only for true bundles (different card names)
        # Keep full price for quantity listings (same card name)
        if listing_id:
            all_items = await db.get_all_items(user["id"])
            listing_items = [i for i in all_items if i.get("ebay_listing_id") == listing_id]

            if len(listing_items) > 1:
                # Check if this is a true bundle (different card names) or quantity listing (same name)
                unique_names = set(i.get("card_name", "") for i in listing_items)
                is_bundle = len(unique_names) > 1

                if is_bundle and item.get("live_price"):
                    # True bundle — divide price equally among items
                    item["live_price"] = round(item["live_price"] / len(listing_items), 2)

        return item
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


class AddItemRequest(BaseModel):
    pc_url: str
    purchase_price: float
    condition: str
    region: str = ""
    card_name: Optional[str] = None
    live_price: Optional[float] = None


@router.post("")
async def add_item(req: AddItemRequest, user: dict = Depends(get_current_user)):
    card_name = req.card_name
    if not card_name:
        slug = req.pc_url.rstrip("/").split("/")[-1]
        card_name = slug.replace("-", " ").title()
    try:
        item_id = await db.add_item(
            user["id"],
            card_name=card_name,
            pc_url=req.pc_url,
            condition=req.condition,
            region=req.region,
            purchase_price=req.purchase_price,
            live_price=req.live_price,
            potential_profit=round((req.live_price or 0) - req.purchase_price, 2) if req.live_price is not None else None,
            status="Inventory",
        )
        return {"item_id": item_id, "message": "Item added"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class PatchItemRequest(BaseModel):
    field: str
    value: str


@router.patch("/{item_id}")
async def patch_item(item_id: int, req: PatchItemRequest, user: dict = Depends(get_current_user)):
    if req.field not in _PATCHABLE_FIELDS:
        raise HTTPException(status_code=400, detail=f"Field '{req.field}' is not patchable")
    try:
        value = _cast_field_value(req.field, req.value)
        await db.edit_item(user["id"], item_id, req.field, value)
        return {"item_id": item_id, "field": req.field, "new_value": value}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


class PatchItemMultiRequest(BaseModel):
    fields: dict  # {field_name: new_value}


@router.patch("/{item_id}/fields")
async def patch_item_multi(item_id: int, req: PatchItemMultiRequest, user: dict = Depends(get_current_user)):
    """Update multiple fields on an item in one request."""
    results = {}
    for field, value in req.fields.items():
        if field not in _PATCHABLE_FIELDS:
            raise HTTPException(status_code=400, detail=f"Field '{field}' is not patchable")
        try:
            cast_value = _cast_field_value(field, value)
            await db.edit_item(user["id"], item_id, field, cast_value)
            results[field] = {"new": cast_value}
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Field '{field}': {e}")
    return {"item_id": item_id, "updated": results}


class SellItemRequest(BaseModel):
    sell_price: float


@router.post("/{item_id}/sell")
async def sell_item_web(item_id: int, req: SellItemRequest, user: dict = Depends(get_current_user)):
    if req.sell_price <= 0:
        return {"success": False, "error": "Sell price must be greater than 0"}
    try:
        result = await db.sell_item(user["id"], item_id, req.sell_price)
        audit.log_mutation("web_sell", item_id, "sold", {
            "sell_price": req.sell_price, "user_id": user["id"],
        })
        return {"success": True, "item_id": item_id, "sell_price": req.sell_price, "result": result}
    except Exception as e:
        return {"success": False, "error": str(e)}


class AddItemWebRequest(BaseModel):
    pc_url: str = ""
    purchase_price: float
    condition: str = "Near mint or better"
    region: str = ""
    source: str = ""
    acquisition_type: str = "purchase"
    traded_item_ids: list[int] = []
    traded_item_names: str = ""
    trade_cash_difference: float = 0.0


class BundleSellRequest(BaseModel):
    item_ids: list[int]
    sell_price: float
    ebay_fee: float = 0.0
    ebay_order_id: str = ""
    date_sold: str


@router.post("/add")
async def add_item_web(req: AddItemWebRequest, user: dict = Depends(get_current_user)):
    from datetime import date
    print(f"[add] === Received POST /inventory/add ===")
    print(f"[add] acquisition_type={req.acquisition_type}, pc_url={req.pc_url}, purchase_price={req.purchase_price}")

    if req.purchase_price <= 0:
        return {"success": False, "error": "Purchase price must be greater than 0"}

    if req.acquisition_type == "purchase":
        if not req.pc_url.strip():
            return {"success": False, "error": "PriceCharting URL is required"}
    else:
        if not req.traded_item_ids:
            return {"success": False, "error": "Trade-in requires at least one item to trade"}

    try:
        card_name = None
        live_price = None

        # Scrape card info if URL provided
        if req.pc_url.strip():
            print(f"[add] Scraping card from {req.pc_url}")
            card_name, live_price = await scraper.scrape_card(req.pc_url, req.condition, req.region)
            print(f"[add] Scrape result: card_name={card_name}, live_price={live_price}")

            if not card_name:
                print(f"[add] ERROR: Could not derive card name from URL")
                return {"success": False, "error": "Could not extract card name from PriceCharting URL"}
        else:
            # For trade-ins without URL, use a placeholder name for now
            card_name = f"Trade-in ({req.traded_item_names or 'items'})"
            live_price = req.purchase_price

        print(f"[add] Adding item: card_name={card_name}, live_price={live_price}, acquisition_type={req.acquisition_type}")
        item_id = await db.add_item(
            user["id"],
            card_name=card_name,
            pc_url=req.pc_url,
            condition=req.condition,
            region=req.region,
            purchase_price=req.purchase_price,
            live_price=live_price,
            potential_profit=round((live_price or 0) - req.purchase_price, 2) if live_price is not None else None,
            source=req.source,
            status="Inventory",
            acquisition_type=req.acquisition_type,
            traded_item_ids=req.traded_item_ids if req.traded_item_ids else None,
            traded_item_names=req.traded_item_names,
            trade_cash_difference=req.trade_cash_difference,
        )
        print(f"[add] Item added: item_id={item_id}")

        # Mark traded items as 'Traded' if this is a trade-in
        if req.acquisition_type == "trade" and req.traded_item_ids:
            from web.database import get_db as _get_db
            database = _get_db()
            today = date.today().isoformat()
            for traded_id in req.traded_item_ids:
                try:
                    traded_item = await db.get_item(user["id"], traded_id)
                    if traded_item:
                        database.table("inventory_items").update({
                            "status": "Traded",
                            "sell_price": traded_item.get("live_price", 0),
                            "profit": 0,
                            "date_sold": today,
                            "acquisition_type": "traded_away"
                        }).eq("item_id", traded_id).eq("user_id", user["id"]).execute()
                        print(f"[add] Marked item {traded_id} as Traded")
                except Exception as e:
                    print(f"[add] Warning: Could not mark item {traded_id} as traded: {e}")

        # Fetch and cache the card image in background
        try:
            cache_key = f"{item_id}_{user['id']}"
            image_url = await _scrape_pc_image(req.pc_url, item_id) if req.pc_url else None
            async with _cache_lock:
                _write_cache(cache_key, image_url)
            print(f"[add] Image cached for item {item_id}: {image_url is not None}")
        except Exception as e:
            print(f"[add] Image cache failed (non-blocking): {e}")

        audit.log_mutation("web_add", item_id, "added", {
            "card_name": card_name, "purchase_price": req.purchase_price,
            "live_price": live_price, "acquisition_type": req.acquisition_type, "user_id": user["id"],
        })
        print(f"[add] SUCCESS: item_id={item_id}, card_name={card_name}")

        return {
            "success":    True,
            "item_id":    item_id,
            "card_name":  card_name,
            "live_price": live_price,
            "margin":     round((live_price or 0) - req.purchase_price, 2),
        }
    except Exception as e:
        import traceback
        print(f"[add] EXCEPTION: {type(e).__name__}: {e}")
        print(f"[add] Traceback:\n{traceback.format_exc()}")
        return {"success": False, "error": str(e)}


@router.post("/bundle-sell")
async def bundle_sell(req: BundleSellRequest, user: dict = Depends(get_current_user)):
    """Sell multiple items as a bundle."""
    from web.database import get_db as _get_db
    import uuid

    print(f"[bundle] === Received POST /inventory/bundle-sell ===")

    item_ids = req.item_ids
    sell_price = req.sell_price
    ebay_fee = req.ebay_fee
    ebay_order_id = req.ebay_order_id
    date_sold = req.date_sold

    if not item_ids or len(item_ids) < 2:
        return {"success": False, "error": "Bundle requires at least 2 items"}
    if sell_price <= 0:
        return {"success": False, "error": "Sale price must be greater than 0"}

    try:
        database = _get_db()
        user_id = user["id"]

        # Get all items
        items_result = database.table("inventory_items")\
            .select("*")\
            .in_("item_id", item_ids)\
            .eq("user_id", user_id)\
            .execute()

        items = items_result.data if items_result.data else []

        if not items:
            return {"success": False, "error": "Items not found"}

        print(f"[bundle] Found {len(items)} items")

        # Calculate totals
        total_cost = sum(float(i.get("purchase_price") or 0) for i in items)
        profit = round(sell_price - ebay_fee - total_cost, 2)
        net_received = round(sell_price - ebay_fee, 2)

        # Create bundle ID and item names
        bundle_id = str(uuid.uuid4())
        item_names = ", ".join(i.get("card_name", "") for i in items)

        print(f"[bundle] Total cost: £{total_cost}, sell price: £{sell_price}, fee: £{ebay_fee}, profit: £{profit}")

        # Distribute sale values equally across items
        sell_price_per_item = round(sell_price / len(items), 2)
        fee_per_item = round(ebay_fee / len(items), 2)

        # Update each item as sold
        for item in items:
            item_cost = float(item.get("purchase_price") or 0)
            item_profit = round(sell_price_per_item - fee_per_item - item_cost, 2)

            database.table("inventory_items").update({
                "status": "Sold",
                "sell_price": sell_price_per_item,
                "ebay_fee": fee_per_item,
                "profit": item_profit,
                "date_sold": date_sold,
                "ebay_order_id": ebay_order_id,
                "bundle_id": bundle_id,
                "postage_cost": 0,
                "fees_verified": False
            }).eq("item_id", item["item_id"]).eq("user_id", user_id).execute()

            print(f"[bundle] Updated item {item['item_id']}: profit £{item_profit}")

        # Save bundle record
        database.table("bundles").insert({
            "user_id": user_id,
            "bundle_name": f"Bundle - {date_sold}",
            "sell_price": sell_price,
            "ebay_fee": ebay_fee,
            "profit": profit,
            "date_sold": date_sold,
            "ebay_order_id": ebay_order_id,
            "item_ids": item_ids,
            "item_names": item_names
        }).execute()

        print(f"[bundle] SUCCESS: Created bundle {bundle_id} with {len(items)} items, profit: £{profit}")
        audit.log_mutation("web_bundle_sell", bundle_id, "bundle_sold", {
            "item_count": len(items), "sell_price": sell_price, "profit": profit, "user_id": user_id
        })

        return {
            "success": True,
            "bundle_id": bundle_id,
            "items_sold": len(items),
            "profit": profit
        }
    except Exception as e:
        import traceback
        print(f"[bundle] EXCEPTION: {type(e).__name__}: {e}")
        print(f"[bundle] Traceback:\n{traceback.format_exc()}")
        return {"success": False, "error": str(e)}


@router.delete("/{item_id}")
async def delete_item(item_id: int, user: dict = Depends(get_current_user)):
    """Remove an item from inventory."""
    try:
        item = await db.get_item(user["id"], item_id)
        if item.get("status") == "Sold":
            return {"success": False, "error": "Cannot remove a sold item"}

        await db.remove_item(user["id"], item_id)
        audit.log_mutation("web_remove", item_id, "removed", {"source": "web_dashboard", "user_id": user["id"]})

        print(f"[web] Removed item {item_id} ({item.get('card_name', '')})")
        return {"success": True, "item_id": item_id}
    except ValueError as e:
        print(f"[web] Remove failed for {item_id}: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        print(f"[web] Remove error for {item_id}: {e}")
        return {"success": False, "error": f"Server error: {e}"}


@router.post("/import-csv")
async def import_csv(
    user: dict = Depends(get_current_user),
    file: UploadFile = File(...),
):
    """
    Import inventory from CSV file.
    Expected columns: Card_Name, PC_URL, Purchase_Price, Condition, Region, Source
    Any column order, case-insensitive headers.
    """
    from web.tier import check_item_limit
    import csv
    import io

    content = await file.read()
    try:
        text = content.decode("utf-8-sig")  # handle BOM
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    # Normalise headers
    rows = []
    for row in reader:
        normalised = {k.strip().lower().replace(" ", "_"): v.strip() for k, v in row.items()}
        rows.append(normalised)

    if not rows:
        return {"success": False, "error": "No rows found in CSV"}

    # Check item limit
    all_items = await db.get_all_items(user["id"])
    current_inv = len([i for i in (all_items or []) if i.get("status") == "Inventory"])

    imported = skipped = errors = 0
    error_details = []

    for i, row in enumerate(rows):
        # Check limit per row
        if not check_item_limit(user, current_inv + imported):
            skipped += len(rows) - i
            error_details.append(f"Stopped at row {i+1} — item limit reached. Upgrade for unlimited.")
            break

        card_name = (row.get("card_name") or row.get("name") or "").strip()
        pc_url = (row.get("pc_url") or row.get("pricecharting_url") or row.get("url") or "").strip()

        if not card_name:
            skipped += 1
            continue

        try:
            purchase_price = float(row.get("purchase_price") or row.get("buy_price") or 0)
        except (ValueError, TypeError):
            purchase_price = 0

        condition = row.get("condition") or "Near mint or better"
        region = row.get("region") or ""
        source = row.get("source") or "CSV Import"

        try:
            await db.add_item(
                user_id=user["id"],
                card_name=card_name,
                pc_url=pc_url,
                condition=condition,
                region=region,
                purchase_price=purchase_price,
                source=source,
            )
            imported += 1
            audit.log_mutation("web_import_csv", 0, "added", {"card": card_name, "user_id": user["id"]})
        except Exception as e:
            errors += 1
            error_details.append(f"Row {i+1} ({card_name}): {str(e)}")

    return {
        "success": True,
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "details": error_details[:10],
    }


# ── Sync existing eBay listing ───────────────────────────────────────────

class SetListingRequest(BaseModel):
    ebay_listing_id: str


@router.post("/{item_id}/set-listing")
async def set_listing(
    item_id: int,
    req: SetListingRequest,
    user: dict = Depends(get_current_user)
):
    """Set the eBay listing ID for an existing eBay listing."""
    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    listing_id = req.ebay_listing_id.strip()
    if not listing_id or not listing_id.replace(" ", "").isdigit():
        raise HTTPException(status_code=400, detail="Invalid listing ID format")

    try:
        # Update the item with the listing ID and mark as listed
        await db.edit_item(user["id"], item_id, "ebay_listing_id", listing_id)
        await db.edit_item(user["id"], item_id, "ebay_listed", "Yes")

        print(f"[web] Set eBay listing for item {item_id}: {listing_id}")
        return {"success": True, "item_id": item_id, "ebay_listing_id": listing_id}
    except Exception as e:
        print(f"[web] Set listing error for {item_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to set listing: {e}")


# ── DEBUG: Test HTTP scraper on Railway ──────────────────────────────────
@router.get("/scrape-test")
async def scrape_test():
    """
    Debug endpoint to test if HTTP requests work for PriceCharting.
    GET /api/inventory/scrape-test
    """
    import requests

    url = "https://www.pricecharting.com/game/pokemon-go/radiant-blastoise-18"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-GB,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
    }

    try:
        print(f"[debug] Testing HTTP request to {url}")
        resp = requests.get(url, headers=headers, timeout=15)
        html_sample = resp.text[:500]

        print(f"[debug] Response status: {resp.status_code}")
        print(f"[debug] Content length: {len(resp.text)}")

        return {
            "status": resp.status_code,
            "success": resp.status_code == 200,
            "content_length": len(resp.text),
            "first_500_chars": html_sample,
            "has_price": "price" in resp.text.lower(),
            "has_pokemon_go": "pokemon-go" in resp.text.lower(),
            "has_completed_auctions": "completed_auctions" in resp.text,
        }
    except Exception as e:
        print(f"[debug] HTTP request failed: {e}")
        return {"error": str(e), "error_type": type(e).__name__}
