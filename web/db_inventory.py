"""
Supabase-backed inventory operations for the web dashboard.
All functions take user_id as first argument for row-level isolation.
The Discord bot still uses excel_db.py — this is web-only.
"""
from datetime import date, datetime
from typing import Optional
from web.database import get_db


# ── Helper ────────────────────────────────────────────────────────────────────

def _row_to_item(row: dict) -> dict:
    """Normalise a Supabase row to the same shape as excel_db returns."""
    return {
        "item_id":           row.get("item_id"),
        "card_name":         row.get("card_name", ""),
        "pc_url":            row.get("pc_url", ""),
        "condition":         row.get("condition", "Near mint or better"),
        "region":            row.get("region", ""),
        "purchase_price":    float(row.get("purchase_price") or 0),
        "live_price":        float(row.get("live_price") or 0) or None,
        "quick_price":       float(row.get("quick_price") or 0) or None,
        "potential_profit":  float(row.get("potential_profit") or 0) or None,
        "sell_price":        float(row.get("sell_price") or 0) or None,
        "profit":            float(row.get("profit") or 0) or None,
        "status":            row.get("status", "Inventory"),
        "quantity":          int(row.get("quantity") or 1),
        "ebay_listing_id":   row.get("ebay_listing_id", ""),
        "ebay_listed":       row.get("ebay_listed", "No"),
        "image_urls":        [u for u in (row.get("image_urls") or "").split("|") if u],
        "date_added":        str(row.get("date_added") or ""),
        "date_sold":         str(row.get("date_sold") or ""),
        "price_verified":    bool(row.get("price_verified")),
        "source":            row.get("source", ""),
    }


# ── Read operations ───────────────────────────────────────────────────────────

async def get_all_items(user_id: str, status_filter: str = None) -> list[dict]:
    db = get_db()
    query = db.table("inventory_items").select("*").eq("user_id", user_id)
    if status_filter:
        query = query.eq("status", status_filter)
    result = query.order("item_id").execute()
    return [_row_to_item(r) for r in (result.data or [])]


async def get_item(user_id: str, item_id: int) -> dict:
    db = get_db()
    result = db.table("inventory_items").select("*") \
        .eq("user_id", user_id).eq("item_id", item_id).single().execute()
    if not result.data:
        raise ValueError(f"Item {item_id} not found")
    return _row_to_item(result.data)


async def get_next_item_id(user_id: str) -> int:
    db = get_db()
    result = db.table("inventory_items").select("item_id") \
        .eq("user_id", user_id).order("item_id", desc=True).limit(1).execute()
    if result.data:
        return result.data[0]["item_id"] + 1
    return 1


# ── Write operations ──────────────────────────────────────────────────────────

async def add_item(user_id: str, **kwargs) -> int:
    db = get_db()
    item_id = await get_next_item_id(user_id)
    db.table("inventory_items").insert({
        "user_id":        user_id,
        "item_id":        item_id,
        "date_added":     str(date.today()),
        **kwargs,
    }).execute()
    return item_id


# Editable fields: request field name -> DB column name. Deliberately an
# allowlist (not a passthrough) so a caller can never target arbitrary
# columns like user_id/item_id via the field parameter.
_EDITABLE_FIELDS = {
    "card_name": "card_name", "pc_url": "pc_url",
    "condition": "condition", "region": "region",
    "purchase_price": "purchase_price", "live_price": "live_price",
    "quick_price": "quick_price", "potential_profit": "potential_profit",
    "sell_price": "sell_price", "profit": "profit",
    "status": "status", "ebay_listing_id": "ebay_listing_id",
    "ebay_listed": "ebay_listed",
    "date_sold": "date_sold", "price_verified": "price_verified",
    "source": "source", "image_urls": "image_urls",
    "ebay_fee": "ebay_fee", "net_received": "net_received",
    "promoted_listing_pct": "promoted_listing_pct", "use_promoted_listing": "use_promoted_listing",
}


async def edit_item(user_id: str, item_id: int, field: str, value) -> None:
    col = _EDITABLE_FIELDS.get(field)
    if col is None:
        raise ValueError(f"Unknown field {field!r}. Valid: {', '.join(_EDITABLE_FIELDS)}")
    db = get_db()
    db.table("inventory_items").update({col: value}) \
        .eq("user_id", user_id).eq("item_id", item_id).execute()


async def sell_item(user_id: str, item_id: int, sell_price: float) -> dict:
    item = await get_item(user_id, item_id)
    profit = sell_price - item["purchase_price"]
    db = get_db()
    db.table("inventory_items").update({
        "status":     "Sold",
        "sell_price": sell_price,
        "profit":     round(profit, 2),
        "date_sold":  str(date.today()),
    }).eq("user_id", user_id).eq("item_id", item_id).execute()
    return {"sell_price": sell_price, "profit": profit}


async def update_sell_price(user_id: str, item_id: int, price: float) -> None:
    db = get_db()
    db.table("inventory_items").update({"sell_price": round(price, 2)}) \
        .eq("user_id", user_id).eq("item_id", item_id).execute()


async def mark_ebay_listed(user_id: str, item_id: int, ebay_listing_id: str) -> None:
    db = get_db()
    db.table("inventory_items").update({
        "ebay_listing_id": ebay_listing_id,
        "ebay_listed":     "Yes",
    }).eq("user_id", user_id).eq("item_id", item_id).execute()


async def mark_ebay_delisted(user_id: str, item_id: int) -> None:
    db = get_db()
    db.table("inventory_items").update({
        "ebay_listing_id": "",
        "ebay_listed":     "No",
    }).eq("user_id", user_id).eq("item_id", item_id).execute()


async def remove_item(user_id: str, item_id: int) -> None:
    item = await get_item(user_id, item_id)
    if item["status"] == "Sold":
        raise ValueError("Cannot remove a sold item")
    db = get_db()
    db.table("inventory_items").delete() \
        .eq("user_id", user_id).eq("item_id", item_id).execute()


async def update_prices(user_id: str, item_id: int, live_price: float,
                         quick_price: float = None, potential_profit: float = None) -> None:
    db = get_db()
    updates = {"live_price": live_price, "price_verified": False}
    if quick_price is not None:
        updates["quick_price"] = quick_price
    if potential_profit is not None:
        updates["potential_profit"] = potential_profit
    db.table("inventory_items").update(updates) \
        .eq("user_id", user_id).eq("item_id", item_id).execute()

    # Record price history
    db.table("price_history").insert({
        "user_id":       user_id,
        "item_id":       item_id,
        "live_price_gbp": live_price,
    }).execute()


# ── Watchlist ─────────────────────────────────────────────────────────────────

async def get_watchlist(user_id: str) -> list[dict]:
    db = get_db()
    result = db.table("watchlist").select("*").eq("user_id", user_id) \
        .order("id").execute()
    return result.data or []


async def add_watch(user_id: str, card_name: str, pc_url: str,
                     target_price: float, current_price: float = None) -> int:
    db = get_db()
    result = db.table("watchlist").insert({
        "user_id":          user_id,
        "card_name":        card_name,
        "pc_url":           pc_url,
        "target_price_gbp": target_price,
        "current_price_gbp": current_price,
    }).execute()
    return result.data[0]["id"]


async def remove_watch(user_id: str, watch_id: int) -> None:
    db = get_db()
    db.table("watchlist").delete() \
        .eq("user_id", user_id).eq("id", watch_id).execute()


# ── Price history ─────────────────────────────────────────────────────────────

async def get_price_history(user_id: str, item_id: int, limit: int = 90) -> list[dict]:
    db = get_db()
    result = db.table("price_history").select("live_price_gbp, recorded_at") \
        .eq("user_id", user_id).eq("item_id", item_id) \
        .order("recorded_at", desc=True).limit(limit).execute()
    rows = result.data or []
    return [{"live_price_gbp": r["live_price_gbp"], "timestamp": r["recorded_at"]}
            for r in reversed(rows)]


# ── Analytics helpers ─────────────────────────────────────────────────────────

async def get_summary_stats(user_id: str) -> dict:
    all_items = await get_all_items(user_id)
    inventory = [i for i in all_items if i["status"] == "Inventory"]
    sold       = [i for i in all_items if i["status"] == "Sold"]

    # Current stock metrics
    cost_in_stock     = sum(i["purchase_price"] for i in inventory)
    potential_value   = sum((i["live_price"] or 0) for i in inventory)
    potential_profit  = potential_value - cost_in_stock  # Potential profit = market value - what we paid

    # Lifetime metrics from sold items
    lifetime_revenue  = sum((i["sell_price"] or 0) for i in sold)
    lifetime_profit   = sum((i["profit"] or 0) for i in sold)
    cost_of_sold      = sum(i["purchase_price"] for i in sold)

    # ROI calculations
    lifetime_roi      = (lifetime_profit / cost_of_sold * 100) if cost_of_sold else 0
    avg_margin_pct    = (lifetime_profit / lifetime_revenue * 100) if lifetime_revenue else 0
    avg_profit_per_sale = lifetime_profit / len(sold) if sold else 0

    # MTD (Month-to-date) metrics
    today = str(date.today())[:7]  # YYYY-MM
    mtd_sold = [i for i in sold if str(i.get("date_sold") or "").startswith(today)]
    mtd_profit = sum((i["profit"] or 0) for i in mtd_sold)

    # 30-day estimate (if we sold mtd_count items per month consistently)
    estimated_30d_profit = (mtd_profit / max(len(mtd_sold), 1)) * 30 if mtd_sold else 0

    return {
        # Stock metrics
        "in_stock":                    len(inventory),
        "sold":                        len(sold),

        # Current inventory value
        "total_cost_in_stock":         round(cost_in_stock, 2),
        "total_potential_in_stock":    round(potential_value, 2),
        "total_potential_profit_in_stock": round(potential_profit, 2),

        # Lifetime metrics
        "total_profit":                round(lifetime_profit, 2),
        "total_revenue":               round(lifetime_revenue, 2),
        "roi_pct":                     round(lifetime_roi, 1),
        "avg_margin_pct":              round(avg_margin_pct, 1),
        "avg_profit":                  round(avg_profit_per_sale, 2),

        # Time-based metrics
        "mtd_profit":                  round(mtd_profit, 2),
        "mtd_sold_count":              len(mtd_sold),
        "est_30d_profit":              round(estimated_30d_profit, 2),
    }


async def get_velocity_stats(user_id: str, group_by: str = "set") -> list[dict]:
    """Average days-to-sell grouped by set, condition, or price range."""
    import re
    from collections import defaultdict

    sold_items = await get_all_items(user_id, status_filter="Sold")

    groups:        dict[str, list[float]] = defaultdict(list)
    group_profits: dict[str, list[float]] = defaultdict(list)

    for item in sold_items:
        date_added = item.get("date_added")
        date_sold  = item.get("date_sold")
        if not date_added or not date_sold:
            continue
        try:
            added = datetime.strptime(str(date_added)[:10], "%Y-%m-%d")
            sold  = datetime.strptime(str(date_sold)[:10],  "%Y-%m-%d")
            days  = max((sold - added).days, 1)
        except ValueError:
            continue

        sell_price     = float(item.get("sell_price")     or 0)
        purchase_price = float(item.get("purchase_price") or 0)
        profit         = sell_price - purchase_price

        if group_by == "set":
            name = item.get("card_name", "")
            m    = re.search(r'\(Pokemon (.+?)\)', name)
            key  = m.group(1) if m else "Unknown"
        elif group_by == "condition":
            key = item.get("condition") or "Unknown"
        elif group_by == "price":
            if sell_price < 3:
                key = "Under £3"
            elif sell_price < 10:
                key = "£3–£10"
            elif sell_price < 25:
                key = "£10–£25"
            elif sell_price < 50:
                key = "£25–£50"
            else:
                key = "£50+"
        else:
            key = "All"

        groups[key].append(days)
        group_profits[key].append(profit)

    results = []
    for key, days_list in groups.items():
        results.append({
            "group":      key,
            "avg_days":   round(sum(days_list) / len(days_list), 1),
            "avg_profit": round(sum(group_profits[key]) / len(group_profits[key]), 2),
            "count":      len(days_list),
        })

    results.sort(key=lambda x: x["avg_days"])
    return results


async def get_restock_suggestions(user_id: str) -> list[dict]:
    """
    Identify sets that sell fast but have low current stock.
    Requires at least 3 historical sales for a set to be included.
    Returns up to 20 suggestions sorted by priority score.
    """
    import re
    from collections import defaultdict

    sold_items = await get_all_items(user_id, status_filter="Sold")
    inv_items  = await get_all_items(user_id, status_filter="Inventory")

    def _set_name(card_name: str) -> str:
        m = re.search(r'\(Pokemon (.+?)\)', card_name or "")
        return m.group(1) if m else "Unknown"

    set_velocity: dict = defaultdict(list)
    for item in sold_items:
        date_added = item.get("date_added")
        date_sold  = item.get("date_sold")
        if not date_added or not date_sold:
            continue
        try:
            added  = datetime.strptime(str(date_added)[:10], "%Y-%m-%d")
            sold   = datetime.strptime(str(date_sold)[:10],  "%Y-%m-%d")
            days   = max((sold - added).days, 1)
            profit = float(item.get("sell_price") or 0) - float(item.get("purchase_price") or 0)
            set_velocity[_set_name(item.get("card_name", ""))].append({"days": days, "profit": profit})
        except Exception:
            continue

    set_stock: dict = defaultdict(int)
    for item in inv_items:
        set_stock[_set_name(item.get("card_name", ""))] += 1

    suggestions = []
    for set_name, sales in set_velocity.items():
        if len(sales) < 3:
            continue
        avg_days      = sum(s["days"]   for s in sales) / len(sales)
        avg_profit    = sum(s["profit"] for s in sales) / len(sales)
        current_stock = set_stock.get(set_name, 0)
        if avg_days < 14 and current_stock < 5:
            suggestions.append({
                "set":            set_name,
                "avg_days":       round(avg_days, 1),
                "avg_profit":     round(avg_profit, 2),
                "current_stock":  current_stock,
                "total_sold":     len(sales),
                "priority_score": round((avg_profit / max(avg_days, 1)) * (5 - current_stock), 2),
            })

    suggestions.sort(key=lambda x: x["priority_score"], reverse=True)
    return suggestions[:20]
