import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import config
import lister_ebay_api
from web import db_inventory as db
from web import user_config
from web.auth import get_current_user
from web.ws_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter()

os.makedirs(config.TEMP_IMAGES_DIR, exist_ok=True)


# ── Sell ──────────────────────────────────────────────────────────────────

class SellRequest(BaseModel):
    item_id: int
    sell_price: float


@router.post("/sell")
async def sell_item(req: SellRequest, user: dict = Depends(get_current_user)):
    try:
        return await db.sell_item(user["id"], req.item_id, req.sell_price)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Reprice-all ───────────────────────────────────────────────────────────

class RepriceAllRequest(BaseModel):
    strategy: str = "quicksell"
    dry_run:  bool = True


@router.post("/reprice-all")
async def reprice_all(req: RepriceAllRequest, user: dict = Depends(get_current_user)):
    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    listed = [
        i for i in all_items
        if i.get("ebay_listed") == "Yes" and i.get("ebay_listing_id")
    ]

    results = []
    for idx, item in enumerate(listed):
        item_id    = item["item_id"]
        listing_id = item["ebay_listing_id"]
        cur_price  = float(item.get("sell_price") or 0)

        if req.strategy == "quicksell":
            new_price = float(item.get("quick_price") or 0) or cur_price
        else:
            new_price = float(item.get("live_price") or 0) or cur_price

        diff    = round(new_price - cur_price, 2)
        applied = False

        if not req.dry_run and new_price > 0 and new_price != cur_price:
            try:
                async with user_config.apply(user):
                    ok = await lister_ebay_api.update_offer_price(listing_id, new_price)
                if ok:
                    await db.update_sell_price(user["id"], item_id, new_price)
                applied = ok
            except Exception:
                applied = False

        results.append({
            "item_id":       item_id,
            "card_name":     item["card_name"],
            "listing_id":    listing_id,
            "current_price": cur_price,
            "new_price":     new_price,
            "diff":          diff,
            "applied":       applied,
        })
        await manager.broadcast({
            "type":    "reprice_progress",
            "current": idx + 1,
            "total":   len(listed),
            "item":    item["card_name"],
        })

    return {
        "dry_run":  req.dry_run,
        "strategy": req.strategy,
        "total":    len(results),
        "items":    results,
    }


# ── List on eBay (multipart — supports photo uploads) ─────────────────────

@router.post("/list-ebay")
async def list_ebay(
    item_id:                int           = Form(...),
    strategy:               str           = Form("quicksell"),
    title:                  str           = Form(""),
    description:            str           = Form(""),
    custom_price:           float         = Form(0.0),
    promoted_listing_pct:   float         = Form(0.0),
    use_promoted_listing:   str           = Form("false"),
    image1:                 Optional[UploadFile] = File(None),
    image2:                 Optional[UploadFile] = File(None),
    image3:                 Optional[UploadFile] = File(None),
    image4:                 Optional[UploadFile] = File(None),
    image5:                 Optional[UploadFile] = File(None),
    user: dict = Depends(get_current_user),
):
    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    live_price     = float(item.get("live_price") or 0)
    quick_price    = float(item.get("quick_price") or 0)
    purchase_price = float(item.get("purchase_price") or 0)
    min_price      = max(round(purchase_price * 1.05, 2), config.EBAY_MIN_PRICE_GBP)

    if strategy == "custom" and custom_price > 0:
        price = max(custom_price, min_price)
    elif strategy == "quicksell" and quick_price >= min_price:
        price = quick_price
    elif strategy == "market" and live_price > 0:
        price = max(round(live_price * 1.15, 2), min_price)
    else:
        price = max(quick_price or round(live_price * 1.15, 2), min_price)

    # Save uploaded images temporarily
    uploads   = [f for f in [image1, image2, image3, image4, image5] if f and f.filename]
    tmp_paths = []
    for upload in uploads:
        safe = "".join(c for c in (upload.filename or "img") if c.isalnum() or c in "._-")
        dest = Path(config.TEMP_IMAGES_DIR) / f"web_{item_id}_{safe}"
        content = await upload.read()
        dest.write_bytes(content)
        tmp_paths.append(dest)

    # If no images provided but stored URLs exist, download them first
    if not tmp_paths:
        stored_urls = item.get("image_urls", [])
        if stored_urls:
            tmp_paths = await _download_images_to_temp(item_id, stored_urls)
            logger.info(f"[images] Reusing {len(tmp_paths)} stored image(s) for item {item_id}")

    try:
        import traceback

        # Prepare promoted listing settings
        use_promo = use_promoted_listing.lower() == 'true' and promoted_listing_pct > 0
        promo_pct = promoted_listing_pct if use_promo else None

        logger.info(f"[listings] Listing item {item_id}: price={price}, use_promo={use_promo}, promo_pct={promo_pct}")

        async with user_config.apply(user):
            # Note: promoted_listing_pct is stored in database for future API integration
            # For now, we only store the preference; eBay API call doesn't support it yet
            result = await lister_ebay_api.list_item_on_ebay(
                item_name   = title or item["card_name"],
                price_gbp   = price,
                image_paths = tmp_paths,
                condition   = item.get("condition") or "",
                description = description,
                region      = item.get("region") or "",
                card_name   = item["card_name"],
                pc_url      = item.get("pc_url") or "",
                item_id     = item_id,
            )
        if result.success and result.listing_url:
            listing_id = result.listing_url.rstrip("/").split("/")[-1].split("?")[0]
            await db.mark_ebay_listed(user["id"], item_id, listing_id)
            await db.update_sell_price(user["id"], item_id, price)
            # Store per-item promotion settings
            if use_promo:
                await db.edit_item(user["id"], item_id, "promoted_listing_pct", promoted_listing_pct)
                await db.edit_item(user["id"], item_id, "use_promoted_listing", True)
        return {
            "success":     result.success,
            "listing_url": result.listing_url,
            "price":       price,
            "error":       result.error,
        }
    except Exception as e:
        import traceback
        logger.info(f"[listings] Error creating eBay listing for item {item_id}: {e}")
        logger.info(traceback.format_exc())
        raise
    finally:
        for p in tmp_paths:
            try:
                p.unlink()
            except OSError:
                pass


async def _download_images_to_temp(item_id: int, urls: list[str]) -> list[Path]:
    """Download stored image URLs to temp dir for reuse in listings."""
    import aiohttp as _aiohttp
    paths = []
    async with _aiohttp.ClientSession() as session:
        for i, url in enumerate(urls[:5]):
            path = Path(config.TEMP_IMAGES_DIR) / f"stored_{item_id}_{i}.jpg"
            try:
                async with session.get(url, timeout=_aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        path.write_bytes(await resp.read())
                        paths.append(path)
            except Exception as e:
                logger.info(f"[images] Failed to download {url}: {e}")
    return paths

# ── Delist from eBay ──────────────────────────────────────────────────────

@router.post("/delist/{item_id}")
async def delist_item(item_id: int, user: dict = Depends(get_current_user)):
    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    listing_id = item.get("ebay_listing_id", "")
    error: Optional[str] = None
    if listing_id:
        try:
            async with user_config.apply(user):
                await lister_ebay_api.end_ebay_listing(listing_id)
        except Exception as exc:
            error = str(exc)

    await db.mark_ebay_delisted(user["id"], item_id)
    return {"success": True, "warning": error}


# ── Verify active eBay listings ───────────────────────────────────────────

class VerifyRequest(BaseModel):
    dry_run: bool = True


@router.post("/verify-all")
async def verify_all_listings(req: VerifyRequest = VerifyRequest(), user: dict = Depends(get_current_user)):
    """Check all eBay-listed inventory items and return which are no longer active."""
    all_items = await db.get_all_items(user["id"])
    listed = [
        i for i in all_items
        if str(i.get("ebay_listed", "")) == "Yes"
        and str(i.get("ebay_listing_id", "") or "").strip().isdigit()
        and str(i.get("status", "")) == "Inventory"
    ]

    ended = []
    for item in listed:
        async with user_config.apply(user):
            is_active = await lister_ebay_api.check_listing_active(str(item["ebay_listing_id"]))
        if not is_active:
            ended.append(item)
            if not req.dry_run:
                await db.edit_item(user["id"], item["item_id"], "ebay_listed",    "No")
                await db.edit_item(user["id"], item["item_id"], "ebay_listing_id", "")
        await asyncio.sleep(0.3)

    return {
        "checked":     len(listed),
        "active":      len(listed) - len(ended),
        "ended":       len(ended),
        "ended_items": [
            {"id": i["item_id"], "name": i["card_name"], "listing_id": i["ebay_listing_id"]}
            for i in ended
        ],
        "dry_run":     req.dry_run,
    }


# ── Apply promotion to all active listings ────────────────────────────────

@router.post("/apply-promotion-all")
async def apply_promotion_to_all(user: dict = Depends(get_current_user)):
    """Apply user's global promoted_listing_pct to all active eBay listings."""
    from web.database import get_db

    # Get user's global promotion percentage
    db_client = get_db()
    profile = db_client.table("user_profiles").select("promoted_listing_pct").eq("id", user["id"]).single().execute()
    promoted_pct = float((profile.data or {}).get("promoted_listing_pct") or 0)

    if promoted_pct <= 0:
        return {"updated": 0, "failed": 0, "errors": ["Global promotion percentage is 0 or not set"]}

    # Get all active eBay listings (status='Inventory' with ebay_listing_id)
    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    active_listings = [
        i for i in all_items
        if i.get("ebay_listing_id") and str(i.get("ebay_listing_id", "")).strip().isdigit()
    ]

    updated = 0
    failed = 0
    errors = []

    for item in active_listings:
        try:
            listing_id = str(item["ebay_listing_id"])
            item_id = item["item_id"]

            # Update via eBay API using Trading API ReviseItem
            async with user_config.apply(user):
                # For now, just update the database; eBay API call would require
                # additional integration with Trading API ReviseItem endpoint
                # This prepares the data for future API integration
                pass

            # Update database with promotion percentage
            await db.edit_item(user["id"], item_id, "promoted_listing_pct", promoted_pct)
            await db.edit_item(user["id"], item_id, "use_promoted_listing", True)
            updated += 1
            logger.info(f"[listings] Updated promotion for item {item_id} (listing {listing_id}) to {promoted_pct}%")

            # Rate limit API calls
            await asyncio.sleep(0.2)

        except Exception as e:
            failed += 1
            errors.append(f"Item {item['item_id']}: {str(e)}")
            logger.info(f"[listings] Failed to update item {item['item_id']}: {e}")

    return {
        "updated": updated,
        "failed": failed,
        "total": len(active_listings),
        "promotion_pct": promoted_pct,
        "errors": errors[:5],  # Return first 5 errors to avoid huge responses
    }


# ── Fetch valid eBay fulfillment policies ─────────────────────────────────

@router.get("/ebay-policies")
async def get_ebay_policies(user: dict = Depends(get_current_user)):
    """Fetch all eBay business policies (fulfillment, payment, return) from eBay Account API."""
    try:
        async with user_config.apply(user):
            fulfillment = await lister_ebay_api.fetch_fulfillment_policies()
            payment = await lister_ebay_api.fetch_payment_policies()
            returns = await lister_ebay_api.fetch_return_policies()
        return {
            "success": True,
            "fulfillment": fulfillment,
            "payment": payment,
            "return": returns,
        }
    except Exception as e:
        import traceback
        logger.info(f"[listings] Error fetching eBay policies: {e}")
        logger.info(traceback.format_exc())
        return {
            "success": False,
            "error": str(e),
            "fulfillment": [],
            "payment": [],
            "return": [],
        }


# ── AI description generator ──────────────────────────────────────────────

class DescRequest(BaseModel):
    item_id:   int
    condition: Optional[str] = None


@router.post("/generate-description")
async def generate_description(req: DescRequest, user: dict = Depends(get_current_user)):
    from web import tier
    import os

    # Check tier access
    if not tier.can(user, "ai_descriptions"):
        raise HTTPException(
            status_code=403,
            detail="Upgrade to Gym Leader or Champion to use AI descriptions"
        )

    try:
        item = await db.get_item(user["id"], req.item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    condition = req.condition or item.get("condition") or "Near mint or better"
    live      = float(item.get("live_price") or 0) or None

    # Determine which API key to use
    plan = user.get("plan", "free")
    user_gemini_key = user.get("gemini_api_key")

    if plan == "champion":
        # Champion uses our managed key
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=500,
                detail="AI description service is temporarily unavailable"
            )
    elif plan == "gym_leader":
        # Gym Leader can use their own key or our key if they haven't set theirs
        api_key = user_gemini_key or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise HTTPException(
                status_code=400,
                detail="Add your Gemini API key in Settings to use AI descriptions"
            )
    else:
        # Trainer has no access
        raise HTTPException(
            status_code=403,
            detail="Upgrade to Gym Leader or Champion to use AI descriptions"
        )

    try:
        import ai_helper  # lazy — requires google-genai package
        async with user_config.apply(user):
            content = await ai_helper.generate_listing_content(
                item_name        = item["card_name"],
                condition        = condition,
                uk_avg_price_gbp = live,
            )
        return {"title": content.get("title", ""), "description": content.get("description", "")}
    except Exception as e:
        return {"title": item["card_name"], "description": "", "error": str(e)}


# ── Sold & Delist (combined action) ───────────────────────────────────────

@router.post("/sold-and-delist/{item_id}")
async def sold_and_delist(item_id: int, body: dict, user: dict = Depends(get_current_user)):
    """Mark item as sold and end the eBay listing in one action."""
    sell_price = float(body.get("sell_price", 0))
    if sell_price <= 0:
        raise HTTPException(status_code=400, detail="Enter a valid sell price")

    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    listing_id = str(item.get("ebay_listing_id") or "").strip()

    results = {"sold": False, "delisted": False, "warning": None}

    # Step 1: End the eBay listing
    if listing_id:
        try:
            async with user_config.apply(user):
                await lister_ebay_api.end_ebay_listing(listing_id)
            results["delisted"] = True
        except Exception as e:
            results["warning"] = f"Could not end listing: {str(e)}"
            logger.info(f"[sold-delist] Could not end listing {listing_id}: {e}")

    # Step 2: Mark as sold in database
    try:
        await db.sell_item(user["id"], item_id, sell_price)
        await db.edit_item(user["id"], item_id, "ebay_listed", "No")
        await db.edit_item(user["id"], item_id, "ebay_listing_id", "")
        results["sold"] = True
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "success": True,
        "sold": results["sold"],
        "delisted": results["delisted"],
        "warning": results["warning"],
        "sell_price": sell_price,
    }


# ── Reprice single listing ─────────────────────────────────────────────────

@router.post("/reprice/{item_id}")
async def reprice_item(item_id: int, body: dict, user: dict = Depends(get_current_user)):
    """Reprice a single eBay listing."""
    new_price = float(body.get("price", 0))
    strategy  = body.get("strategy", "custom")

    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    listing_id = str(item.get("ebay_listing_id", "")).strip()
    if not listing_id:
        return {"success": False, "error": "No eBay listing ID found"}

    if strategy == "quicksell":
        new_price = float(item.get("quick_price") or item.get("live_price") or 0) * 0.93
    elif strategy == "market":
        new_price = float(item.get("live_price") or 0) * 1.15

    purchase_price = float(item.get("purchase_price") or 0)
    min_price = max(round(purchase_price * 1.10, 2), 0.99)
    new_price = max(round(new_price, 2), min_price)

    if new_price <= 0:
        return {"success": False, "error": "Could not determine a valid price"}

    sku = f"pokemaz-{item_id}"
    async with user_config.apply(user):
        success = await lister_ebay_api.revise_listing_price(listing_id, new_price, sku=sku)

    if success:
        await db.update_sell_price(user["id"], item_id, new_price)

    return {"success": success, "new_price": new_price, "listing_id": listing_id}


@router.post("/reprice-all-v2")
async def reprice_all_listings_v2(body: dict, user: dict = Depends(get_current_user)):
    """Reprice all active eBay listings with strategy/discount support."""
    strategy  = body.get("strategy", "quicksell")
    discount  = float(body.get("discount_pct", 7)) / 100
    dry_run   = body.get("dry_run", False)

    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    listed = [i for i in all_items
              if str(i.get("ebay_listed", "")) == "Yes"
              and str(i.get("ebay_listing_id", "") or "").strip().isdigit()]

    results = {"updated": 0, "skipped": 0, "failed": 0, "items": []}

    for item in listed:
        live    = float(item.get("live_price") or 0)
        quick   = float(item.get("quick_price") or 0)
        cost    = float(item.get("purchase_price") or 0)
        min_p   = max(round(cost * 1.10, 2), 0.99)
        current = float(item.get("sell_price") or 0)

        if strategy == "quicksell":
            new_price = max(quick or round(live * (1 - discount), 2), min_p)
        elif strategy == "market":
            new_price = max(round(live * 1.15, 2), min_p)
        else:
            new_price = max(round(live * (1 - discount), 2), min_p)

        new_price = round(new_price, 2)
        diff = abs(new_price - current)

        if diff < 0.10:
            results["skipped"] += 1
            continue

        results["items"].append({
            "item_id":   item["item_id"],
            "card_name": item["card_name"],
            "old_price": current,
            "new_price": new_price,
            "change":    round(new_price - current, 2),
        })

        if not dry_run:
            sku = f"pokemaz-{item['item_id']}"
            async with user_config.apply(user):
                ok = await lister_ebay_api.revise_listing_price(
                    str(item["ebay_listing_id"]), new_price, sku=sku
                )
            if ok:
                await db.update_sell_price(user["id"], item["item_id"], new_price)
                results["updated"] += 1
            else:
                results["failed"] += 1
            await asyncio.sleep(0.3)

    return results


# ── Bundle List on eBay ───────────────────────────────────────────────────

class BundleListRequest(BaseModel):
    item_ids: list[int]
    title: str
    price: float
    promoted_listing_pct: float = 0
    description: str = ""
    photos: list[str] = []  # base64 encoded images


@router.post("/bundle-list")
async def bundle_list_on_ebay(req: BundleListRequest, user: dict = Depends(get_current_user)):
    """List inventory items as a single eBay bundle listing."""
    user_id = user["id"]
    item_ids = [int(i) if isinstance(i, str) else i for i in (req.item_ids or [])]
    title = req.title[:80]
    price = req.price
    promo_pct = req.promoted_listing_pct
    description = req.description

    try:
        if not item_ids:
            return {"success": False, "error": "No items selected"}

        # Get all items
        all_items = await db.get_all_items(user_id, status_filter="Inventory")
        items = [i for i in all_items if i["item_id"] in item_ids]

        if not items:
            return {"success": False, "error": "Items not found"}

        # Generate bundle ID
        import uuid
        bundle_id = str(uuid.uuid4())

        # Use first item's data as base for listing
        first_item = items[0]

        # Convert base64 photos to temporary files
        image_paths = []
        photos = req.photos or []
        for idx, photo_b64 in enumerate(photos):
            try:
                if photo_b64.startswith('data:image'):
                    # Remove data URI prefix
                    photo_b64 = photo_b64.split(',', 1)[1]

                import base64
                image_data = base64.b64decode(photo_b64)

                # Save to temp directory
                safe_name = f"bundle_{bundle_id[:8]}_photo_{idx}.jpg"
                dest = Path(config.TEMP_IMAGES_DIR) / safe_name
                dest.write_bytes(image_data)
                image_paths.append(dest)
                logger.info(f"[bundle-list] Saved photo {idx+1}: {dest}")
            except Exception as e:
                logger.info(f"[bundle-list] Error converting photo {idx}: {e}")

        # Create eBay listing using existing lister
        async with user_config.apply(user):
            result = await lister_ebay_api.list_item_on_ebay(
                item_name=title,
                price_gbp=price,
                image_paths=image_paths,
                condition="Near Mint",
                description=description,
                card_name=title,
                pc_url=first_item.get("pc_url", ""),
                item_id=item_ids[0],
                sku=f"pokemaz-bundle-{bundle_id[:8]}"
            )

        if not result or not result.success:
            error_msg = result.error if result else "Unknown error"
            return {"success": False, "error": f"Failed to create eBay listing: {error_msg}"}

        listing_url = result.listing_url if result.listing_url else None
        listing_id = listing_url.rstrip("/").split("/")[-1].split("?")[0] if listing_url else None

        if not listing_id:
            return {"success": False, "error": "Could not extract listing ID"}

        # Update all items with bundle listing info in database
        for item in items:
            await db.update_item_field(
                user_id,
                item["item_id"],
                "ebay_listing_id",
                listing_id
            )
            await db.update_item_field(user_id, item["item_id"], "ebay_listed", "Yes")
            await db.update_item_field(user_id, item["item_id"], "bundle_id", bundle_id)
            await db.update_item_field(user_id, item["item_id"], "promoted_listing_pct", promo_pct)

        return {
            "success": True,
            "listing_id": listing_id,
            "bundle_id": bundle_id,
            "listing_url": listing_url
        }

    except Exception as e:
        logger.info(f"[bundle-list] Error: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


# ── Sync eBay listing prices ────────────────────────────────────────────────

@router.post("/sync-prices")
async def sync_listing_prices(user: dict = Depends(get_current_user)):
    """Fetch current eBay listing prices and sync back to inventory.

    For bundle listings (multiple items with same ebay_listing_id):
    - Same-name items (quantity listing): all get full price
    - Different-name items (true bundle): split price equally
    """
    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    listed = [
        i for i in all_items
        if str(i.get("ebay_listed", "")) == "Yes"
        and str(i.get("ebay_listing_id", "") or "").strip().isdigit()
    ]

    # Group items by listing ID to detect bundles
    listings_by_id = {}
    for item in listed:
        listing_id = item.get("ebay_listing_id")
        if listing_id:
            if listing_id not in listings_by_id:
                listings_by_id[listing_id] = []
            listings_by_id[listing_id].append(item)

    # Detect true bundles (different card names) vs quantity listings (same name)
    bundles = {}
    for listing_id, listing_items in listings_by_id.items():
        if len(listing_items) > 1:
            unique_names = set(i.get("card_name", "") for i in listing_items)
            bundles[listing_id] = len(unique_names) > 1

    updated = failed = 0
    results = []

    # Fetch prices from eBay and sync to inventory
    for listing_id, listing_items in listings_by_id.items():
        try:
            async with user_config.apply(user):
                offer = await lister_ebay_api.get_offer_details(listing_id)

            if not offer:
                logger.info(f"[sync-prices] No offer found for listing {listing_id}")
                failed += len(listing_items)
                results.append({
                    "listing_id": listing_id,
                    "items_count": len(listing_items),
                    "status": "no_offer",
                    "price": None,
                })
                continue

            ebay_price = offer.get("current_price")
            if not ebay_price:
                logger.info(f"[sync-prices] No price in offer for listing {listing_id}: {offer}")
                failed += len(listing_items)
                results.append({
                    "listing_id": listing_id,
                    "items_count": len(listing_items),
                    "status": "no_price",
                    "price": None,
                })
                continue

            logger.info(f"[sync-prices] Fetched listing {listing_id}: £{ebay_price} ({len(listing_items)} items)")

            # Log for specific test listings
            if listing_id in ("336711544234", "336711556909"):
                logger.info(f"[sync-prices] TEST LISTING {listing_id}: fetched £{ebay_price}")
                logger.info(f"[sync-prices]   Full eBay response: {offer}")
                logger.info(f"[sync-prices]   Items sharing this listing: {len(listing_items)}")
                for item in listing_items:
                    logger.info(f"[sync-prices]     - Item {item['item_id']}: {item['card_name']}")

            # Determine price per item
            is_bundle = bundles.get(listing_id, False)
            if len(listing_items) > 1 and is_bundle:
                # True bundle: divide price equally
                price_per_item = round(ebay_price / len(listing_items), 2)
                logger.info(f"[sync-prices] True bundle: dividing £{ebay_price} by {len(listing_items)} = £{price_per_item} per item")
            else:
                # Quantity listing or single item: keep full price
                price_per_item = ebay_price
                if len(listing_items) > 1:
                    logger.info(f"[sync-prices] Quantity listing: keeping £{ebay_price} for each of {len(listing_items)} items")

            # Update each item
            for item in listing_items:
                try:
                    await db.update_sell_price(user["id"], item["item_id"], price_per_item)
                    updated += 1
                    logger.info(f"[sync-prices] Updated item {item['item_id']}: £{price_per_item}")
                except Exception as e:
                    failed += 1
                    logger.info(f"[sync-prices] Failed to update item {item['item_id']}: {e}")

            results.append({
                "listing_id": listing_id,
                "items_count": len(listing_items),
                "is_bundle": is_bundle,
                "ebay_price": ebay_price,
                "price_per_item": price_per_item,
                "status": "success",
            })

        except Exception as e:
            failed += len(listing_items)
            logger.info(f"[sync-prices] Error fetching listing {listing_id}: {e}")
            results.append({
                "listing_id": listing_id,
                "items_count": len(listing_items),
                "status": "error",
                "error": str(e),
            })

    return {
        "total": len(listed),
        "updated": updated,
        "failed": failed,
        "results": results,
    }
