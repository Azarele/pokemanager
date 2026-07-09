import asyncio

from fastapi import APIRouter, Depends, HTTPException

import lister_ebay_api
import scraper
from web import db_inventory as db
from web import user_config
from web.auth import get_current_user

router = APIRouter()


@router.get("/competitors/{item_id}")
async def get_competitor_prices(item_id: int, user: dict = Depends(get_current_user)):
    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    card_name = item.get("card_name", "")
    condition = item.get("condition", "")
    try:
        async with user_config.apply(user):
            comp = await lister_ebay_api.get_competitor_price(card_name, condition)
        return {
            "item_id":   item_id,
            "card_name": card_name,
            "competitors": comp,
            "our_price": item.get("sell_price") or item.get("quick_price"),
            "market":    item.get("live_price"),
        }
    except Exception as e:
        return {"item_id": item_id, "error": str(e), "competitors": None}


@router.get("/check/{item_id}")
async def check_price(item_id: int, user: dict = Depends(get_current_user)):
    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    async with user_config.apply(user):
        competitors = await lister_ebay_api.get_competitor_price(
            item["card_name"], item.get("condition", "")
        )
    return {
        "item_id":      item_id,
        "card_name":    item["card_name"],
        "live_price":   item.get("live_price"),
        "quick_price":  item.get("quick_price"),
        "competitors":  competitors,
    }


@router.post("/refresh/{item_id}")
async def refresh_single_price(item_id: int, user: dict = Depends(get_current_user)):
    """Refresh live price (and competitor-aware quick price) for a single item."""
    try:
        item = await db.get_item(user["id"], item_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    pc_url = item.get("pc_url") or ""
    if not pc_url:
        return {"success": False, "error": "No PriceCharting URL"}

    condition = item.get("condition") or "Near mint or better"
    region    = item.get("region") or ""

    try:
        card_name, live_price = await scraper.scrape_card(pc_url, condition, region)
        if live_price is None:
            return {"success": False, "error": "Could not scrape price"}

        purchase  = float(item.get("purchase_price") or 0)
        potential = round(live_price - purchase, 2)

        try:
            async with user_config.apply(user):
                comp = await lister_ebay_api.get_competitor_price(
                    item["card_name"], condition
                )
            quick_price = comp.get("quick_sell") if comp else round(live_price * 0.93, 2)
        except Exception:
            quick_price = round(live_price * 0.93, 2)

        await db.update_prices(user["id"], item_id, live_price, quick_price, potential)

        return {
            "success":          True,
            "item_id":          item_id,
            "live_price":       live_price,
            "quick_price":      quick_price,
            "potential_profit": potential,
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.post("/refresh-bulk")
async def refresh_bulk_prices(body: dict, user: dict = Depends(get_current_user)):
    """Refresh prices for a list of item IDs (or all in-stock items if item_ids is empty)."""
    item_ids = body.get("item_ids", [])

    if not item_ids:
        all_items = await db.get_all_items(user["id"], status_filter="Inventory")
        item_ids = [i["item_id"] for i in all_items if i.get("pc_url")]

    updated = failed = 0

    for item_id in item_ids:
        try:
            item = await db.get_item(user["id"], item_id)
            pc_url = item.get("pc_url") or ""
            if not pc_url:
                failed += 1
                continue

            condition = item.get("condition") or "Near mint or better"
            region    = item.get("region") or ""
            card_name, live_price = await scraper.scrape_card(pc_url, condition, region)
            if live_price is None:
                failed += 1
                continue

            purchase    = float(item.get("purchase_price") or 0)
            potential   = round(live_price - purchase, 2)
            quick_price = round(live_price * 0.93, 2)

            await db.update_prices(user["id"], item_id, live_price, quick_price, potential)
            updated += 1

        except Exception as e:
            print(f"[price-refresh] Error for item {item_id}: {e}")
            failed += 1

        await asyncio.sleep(0.5)  # avoid rate-limiting PriceCharting

    return {
        "success": True,
        "updated": updated,
        "failed":  failed,
        "total":   len(item_ids),
    }
