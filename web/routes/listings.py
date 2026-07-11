import asyncio
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import audit
import config
import lister_ebay_api
from web import db_inventory as db
from web import user_config
from web.auth import get_current_user
from web.ws_manager import manager

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
            print(f"[images] Reusing {len(tmp_paths)} stored image(s) for item {item_id}")

    try:
        # Prepare promoted listing settings
        use_promo = use_promoted_listing.lower() == 'true' and promoted_listing_pct > 0
        promo_pct = promoted_listing_pct if use_promo else None

        async with user_config.apply(user):
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
                promoted_listing_pct = promo_pct,
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
                print(f"[images] Failed to download {url}: {e}")
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
            print(f"[sold-delist] Could not end listing {listing_id}: {e}")

    # Step 2: Mark as sold in database
    try:
        await db.sell_item(user["id"], item_id, sell_price)
        await db.edit_item(user["id"], item_id, "ebay_listed", "No")
        await db.edit_item(user["id"], item_id, "ebay_listing_id", "")
        results["sold"] = True
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Step 3: Audit log
    audit.log_mutation("web_sold_delist", item_id, "sold_and_delisted", {
        "sell_price": sell_price,
        "listing_id": listing_id,
        "user_id": user["id"],
    })

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
        audit.log_mutation("web_reprice", item_id, "repriced", {
            "new_price": new_price, "strategy": strategy, "listing_id": listing_id
        })

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
