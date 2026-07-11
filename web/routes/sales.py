import asyncio
import json
from datetime import date, timedelta, datetime as _dt

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from web import db_inventory as db
from web.auth import get_current_user
from web.notifications import send_discord_notification

router = APIRouter()


@router.get("/today")
async def get_today_sales(user: dict = Depends(get_current_user)):
    today = date.today().strftime("%Y-%m-%d")
    all_items = await db.get_all_items(user["id"], status_filter="Sold")
    today_sales = [i for i in all_items if str(i.get("date_sold", "") or "").startswith(today)]
    revenue = sum(float(i.get("sell_price") or 0) for i in today_sales)
    cost    = sum(float(i.get("purchase_price") or 0) for i in today_sales)
    # Use actual profit from database (includes fee deductions) instead of simple margin
    profit  = sum(float(i.get("profit") or 0) for i in today_sales)
    return {
        "date":    today,
        "sales":   today_sales,
        "count":   len(today_sales),
        "revenue": round(revenue, 2),
        "cost":    round(cost, 2),
        "profit":  round(profit, 2),
    }


@router.get("/week")
async def get_week_sales(user: dict = Depends(get_current_user)):
    all_items = await db.get_all_items(user["id"], status_filter="Sold")
    days: dict[str, dict] = {}
    for i in range(7):
        d = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        days[d] = {"date": d, "sales": [], "revenue": 0.0, "profit": 0.0, "count": 0}

    for item in all_items:
        sold_date = str(item.get("date_sold", "") or "")[:10]
        if sold_date in days:
            sell = float(item.get("sell_price") or 0)
            cost = float(item.get("purchase_price") or 0)
            days[sold_date]["sales"].append(item)
            days[sold_date]["revenue"] += sell
            days[sold_date]["profit"]  += sell - cost
            days[sold_date]["count"]   += 1

    result = sorted(days.values(), key=lambda x: x["date"], reverse=True)
    for d in result:
        d["revenue"] = round(d["revenue"], 2)
        d["profit"]  = round(d["profit"], 2)
    return {"days": result, "total_days": 7}


@router.get("/month")
async def get_month_sales(user: dict = Depends(get_current_user)):
    prefix    = date.today().strftime("%Y-%m")
    all_items = await db.get_all_items(user["id"], status_filter="Sold")
    month_sales = [i for i in all_items if str(i.get("date_sold", "") or "").startswith(prefix)]
    revenue = sum(float(i.get("sell_price") or 0) for i in month_sales)
    cost    = sum(float(i.get("purchase_price") or 0) for i in month_sales)
    return {
        "month":   prefix,
        "count":   len(month_sales),
        "revenue": round(revenue, 2),
        "profit":  round(revenue - cost, 2),
        "sales":   month_sales,
    }


@router.get("/trend")
async def get_profit_trend(days: int = Query(90), user: dict = Depends(get_current_user)):
    """Daily profit aggregated over the last N days for trend chart."""
    all_sold = await db.get_all_items(user["id"], status_filter="Sold")

    trend = {}
    for i in range(days):
        d = (date.today() - timedelta(days=i)).strftime("%Y-%m-%d")
        trend[d] = {"date": d, "profit": 0.0, "revenue": 0.0, "count": 0}

    for item in all_sold:
        sold_date = str(item.get("date_sold") or "")[:10]
        if sold_date not in trend:
            continue
        sell = float(item.get("sell_price") or 0)
        cost = float(item.get("purchase_price") or 0)
        trend[sold_date]["profit"]  += sell - cost
        trend[sold_date]["revenue"] += sell
        trend[sold_date]["count"]   += 1

    result = sorted(trend.values(), key=lambda x: x["date"])
    profits = [d["profit"] for d in result]
    for i, day in enumerate(result):
        window = profits[max(0, i - 6):i + 1]
        day["rolling_avg"] = round(sum(window) / len(window), 2)
        day["profit"]      = round(day["profit"], 2)
        day["revenue"]     = round(day["revenue"], 2)

    return {"days": result, "period": days}


@router.get("/stream")
async def sales_stream(user: dict = Depends(get_current_user)):
    user_id = user["id"]

    async def event_generator():
        while True:
            today     = date.today().strftime("%Y-%m-%d")
            all_items = await db.get_all_items(user_id, status_filter="Sold")
            today_sales = [i for i in all_items if str(i.get("date_sold", "") or "").startswith(today)]
            revenue = sum(float(i.get("sell_price") or 0) for i in today_sales)
            cost    = sum(float(i.get("purchase_price") or 0) for i in today_sales)
            data    = json.dumps({
                "count":   len(today_sales),
                "revenue": round(revenue, 2),
                "profit":  round(revenue - cost, 2),
            })
            yield f"data: {data}\n\n"
            await asyncio.sleep(30)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/events")
async def global_events(user: dict = Depends(get_current_user)):
    """SSE stream for all real-time events — new sales, heartbeats."""
    user_id = user["id"]

    async def event_generator():
        last_sold_count = 0
        while True:
            try:
                today     = date.today().strftime("%Y-%m-%d")
                all_items = await db.get_all_items(user_id, status_filter="Sold")
                today_sales = [i for i in all_items if str(i.get("date_sold", "") or "").startswith(today)]
                current_count = len(today_sales)

                if current_count > last_sold_count and last_sold_count > 0:
                    newest     = today_sales[-1]
                    sell_price = float(newest.get("sell_price") or 0)
                    profit     = sell_price - float(newest.get("purchase_price") or 0)
                    card_name  = newest.get("card_name", "Unknown")
                    event_data = json.dumps({
                        "type":        "new_sale",
                        "card_name":   card_name,
                        "sell_price":  sell_price,
                        "profit":      round(profit, 2),
                        "item_id":     newest.get("item_id"),
                        "today_count": current_count,
                    })
                    yield f"event: sale\ndata: {event_data}\n\n"
                    await send_discord_notification(
                        user_id,
                        "💰 Item Sold",
                        f"**{card_name}** sold for **£{sell_price:.2f}**\nProfit: £{profit:.2f}",
                        5763719,
                    )

                last_sold_count = current_count
                yield f"event: heartbeat\ndata: {json.dumps({'count': current_count, 'ts': today})}\n\n"
            except Exception as e:
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
            await asyncio.sleep(30)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/by-date")
async def sales_by_date(
    date_str: str = Query(..., alias="date"),  # YYYY-MM-DD
    user: dict = Depends(get_current_user)
):
    """Return all sales for a specific date with full fee breakdown."""
    from web.database import get_db as _get_db

    database = _get_db()

    # Get all items sold on this date
    result = database.table("inventory_items").select("*")\
        .eq("user_id", user["id"])\
        .eq("status", "Sold")\
        .eq("date_sold", date_str)\
        .execute()

    items = result.data or []

    ebay_fee_rate = float(user.get("ebay_fee_rate") or 0.1235)
    # Postage is always £0 for eBay sales - buyer pays via Simple Delivery
    postage_cost  = 0.00

    sales = []
    for item in items:
        sell_price     = float(item.get("sell_price") or 0)
        purchase_price = float(item.get("purchase_price") or 0)

        # Use stored database values, not recalculated estimates
        # eBay fee should be the actual value from the order, not 12.35% estimate
        ebay_fee      = item.get("ebay_fee") or round(sell_price * ebay_fee_rate, 2)
        # Profit already calculated with actual fees in sync process
        profit        = item.get("profit") or round(sell_price - purchase_price, 2)
        net_received  = item.get("net_received") or round(sell_price - ebay_fee - postage_cost, 2)
        roi_pct       = round((profit / purchase_price * 100), 1) if purchase_price > 0 else 0

        sales.append({
            "item_id":         item["item_id"],
            "card_name":       item["card_name"],
            "condition":       item.get("condition", ""),
            "source":          item.get("source", ""),
            "purchase_price":  purchase_price,
            "sell_price":      sell_price,
            "ebay_fee":        ebay_fee,
            "postage_cost":    postage_cost,
            "net_received":    net_received,
            "profit":          profit,
            "roi_pct":         roi_pct,
            "ebay_listing_id": item.get("ebay_listing_id", ""),
            "date_sold":       item.get("date_sold", ""),
        })

    total_revenue  = round(sum(s["sell_price"] for s in sales), 2)
    total_fees     = round(sum(s["ebay_fee"] for s in sales), 2)
    total_postage  = round(sum(s["postage_cost"] for s in sales), 2)
    total_cost     = round(sum(s["purchase_price"] for s in sales), 2)
    total_profit   = round(sum(s["profit"] for s in sales), 2)
    total_net      = round(sum(s["net_received"] for s in sales), 2)

    return {
        "date":    date_str,
        "sales":   sales,
        "count":   len(sales),
        "summary": {
            "total_revenue":   total_revenue,
            "total_fees":      total_fees,
            "total_postage":   total_postage,
            "total_cost":      total_cost,
            "total_net":       total_net,
            "total_profit":    total_profit,
            "avg_roi":         round(sum(s["roi_pct"] for s in sales) / len(sales), 1) if sales else 0,
        }
    }
