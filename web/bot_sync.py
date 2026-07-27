"""
bot_sync.py — keeps Supabase in sync when the Discord bot mutates inventory.
Called after every excel_db write. Errors are swallowed so bot never breaks.
"""
import os
from web.database import get_db

_USER_ID = os.getenv("BOT_OWNER_USER_ID", "")


def _db():
    try:
        return get_db()
    except Exception:
        return None


def sync_add_item(item: dict) -> None:
    try:
        db = _db()
        if not db:
            return
        db.table("inventory_items").upsert({
            "user_id":          _USER_ID,
            "item_id":          item["item_id"],
            "card_name":        item.get("card_name", ""),
            "pc_url":           item.get("pc_url", ""),
            "condition":        item.get("condition", "Near mint or better"),
            "region":           item.get("region", ""),
            "purchase_price":   float(item.get("purchase_price") or 0),
            "live_price":       float(item.get("live_price") or 0) or None,
            "quick_price":      float(item.get("quick_price") or 0) or None,
            "potential_profit": float(item.get("potential_profit") or 0) or None,
            "status":           item.get("status", "Inventory"),
            "ebay_listing_id":  str(item.get("ebay_listing_id") or ""),
            "ebay_listed":      str(item.get("ebay_listed") or "No"),
            "date_added":       str(item.get("date_added") or "")[:10] or None,
            "source":           str(item.get("source") or ""),
        }, on_conflict="user_id,item_id").execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_add_item failed: {e}")


def sync_sell_item(item_id: int, sell_price: float, profit: float) -> None:
    try:
        db = _db()
        if not db:
            return
        from datetime import date
        db.table("inventory_items").update({
            "status":     "Sold",
            "sell_price": sell_price,
            "profit":     profit,
            "date_sold":  str(date.today()),
        }).eq("user_id", _USER_ID).eq("item_id", item_id).execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_sell_item failed: {e}")


def sync_unsell_item(item_id: int) -> None:
    """Called when a cancelled eBay order reverts a Sold item back to Inventory."""
    try:
        db = _db()
        if not db:
            return
        db.table("inventory_items").update({
            "status":     "Inventory",
            "sell_price": None,
            "profit":     None,
            "date_sold":  None,
        }).eq("user_id", _USER_ID).eq("item_id", item_id).execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_unsell_item failed: {e}")


def sync_remove_item(item_id: int) -> None:
    try:
        db = _db()
        if not db:
            return
        db.table("inventory_items").delete() \
            .eq("user_id", _USER_ID).eq("item_id", item_id).execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_remove_item failed: {e}")


def sync_price_update(item_id: int, live_price: float, quick_price: float = None,
                       potential_profit: float = None) -> None:
    try:
        db = _db()
        if not db:
            return
        updates = {"live_price": live_price, "price_verified": False}
        if quick_price is not None:
            updates["quick_price"] = quick_price
        if potential_profit is not None:
            updates["potential_profit"] = potential_profit
        db.table("inventory_items").update(updates) \
            .eq("user_id", _USER_ID).eq("item_id", item_id).execute()
        # Also record price history
        db.table("price_history").insert({
            "user_id":        _USER_ID,
            "item_id":        item_id,
            "live_price_gbp": live_price,
        }).execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_price_update failed: {e}")


def sync_ebay_listed(item_id: int, listing_id: str, price: float) -> None:
    try:
        db = _db()
        if not db:
            return
        db.table("inventory_items").update({
            "ebay_listing_id": listing_id,
            "ebay_listed":     "Yes",
            "sell_price":      price,
        }).eq("user_id", _USER_ID).eq("item_id", item_id).execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_ebay_listed failed: {e}")


def sync_ebay_delisted(item_id: int) -> None:
    try:
        db = _db()
        if not db:
            return
        db.table("inventory_items").update({
            "ebay_listing_id": "",
            "ebay_listed":     "No",
        }).eq("user_id", _USER_ID).eq("item_id", item_id).execute()
    except Exception as e:
        logger.info(f"[bot_sync] sync_ebay_delisted failed: {e}")


def sync_ebay_sold(item_id: int, sell_price: float, profit: float) -> None:
    """Called when eBay auto-sale detection fires."""
    sync_sell_item(item_id, sell_price, profit)
