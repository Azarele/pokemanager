import csv
import io
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from web import db_inventory as db
from web.auth import get_current_user

router = APIRouter()


@router.get("/summary")
async def get_summary(user: dict = Depends(get_current_user)):
    return await db.get_summary_stats(user["id"])


@router.get("/velocity")
async def get_velocity(group_by: str = Query("set"), user: dict = Depends(get_current_user)):
    return await db.get_velocity_stats(user["id"], group_by)


@router.get("/efficiency")
async def get_efficiency(user: dict = Depends(get_current_user)):
    sold = await db.get_all_items(user["id"], status_filter="Sold")
    results = []
    for item in sold:
        date_added = item.get("date_added")
        date_sold  = item.get("date_sold")
        if not date_added or not date_sold:
            continue
        try:
            added   = datetime.strptime(str(date_added)[:10], "%Y-%m-%d")
            sold_dt = datetime.strptime(str(date_sold)[:10],  "%Y-%m-%d")
            days    = max((sold_dt - added).days, 1)
        except ValueError:
            continue
        profit = float(item.get("profit") or 0)
        results.append({
            "item_id":        item["item_id"],
            "card_name":      item["card_name"],
            "days_to_sell":   days,
            "profit":         profit,
            "purchase_price": item.get("purchase_price"),
            "sell_price":     item.get("sell_price"),
        })

    avg_days   = sum(r["days_to_sell"] for r in results) / len(results) if results else 14.0
    avg_profit = sum(r["profit"]       for r in results) / len(results) if results else 0.0

    for r in results:
        fast       = r["days_to_sell"] < avg_days
        profitable = r["profit"] > avg_profit
        if fast and profitable:
            r["quadrant"] = "Fast & profitable"
        elif not fast and profitable:
            r["quadrant"] = "Slow & profitable"
        elif fast:
            r["quadrant"] = "Fast & cheap"
        else:
            r["quadrant"] = "Avoid"

    return {"items": results, "avg_days": round(avg_days, 1), "avg_profit": round(avg_profit, 2)}


@router.get("/by-source")
async def analytics_by_source(user: dict = Depends(get_current_user)):
    sold = await db.get_all_items(user["id"], status_filter="Sold")
    sources: dict = defaultdict(lambda: {"count": 0, "revenue": 0.0, "cost": 0.0, "profit": 0.0})
    for item in sold:
        src  = str(item.get("source") or "Unknown") or "Unknown"
        sell = float(item.get("sell_price") or 0)
        cost = float(item.get("purchase_price") or 0)
        sources[src]["count"]   += 1
        sources[src]["revenue"] += sell
        sources[src]["cost"]    += cost
        sources[src]["profit"]  += sell - cost
    result = []
    for src, d in sources.items():
        roi = (d["profit"] / d["cost"] * 100) if d["cost"] else 0
        result.append({
            "source":  src,
            "count":   d["count"],
            "revenue": round(d["revenue"], 2),
            "profit":  round(d["profit"], 2),
            "roi_pct": round(roi, 1),
        })
    return {"sources": sorted(result, key=lambda x: x["profit"], reverse=True)}


def _filter_by_period(items: list, year: int = None, month: int = None) -> list:
    if not year and not month:
        return items
    result = []
    for item in items:
        date_sold = str(item.get("date_sold") or "")[:10]
        if not date_sold:
            continue
        if year and not date_sold.startswith(str(year)):
            continue
        if month and not date_sold.startswith(f"{year or datetime.now().year}-{month:02d}"):
            continue
        result.append(item)
    return result


@router.get("/export/hmrc")
async def export_hmrc_csv(year: int = None, month: int = None, user: dict = Depends(get_current_user)):
    all_sold = await db.get_all_items(user["id"], status_filter="Sold")
    filtered = sorted(_filter_by_period(all_sold, year, month),
                      key=lambda x: str(x.get("date_sold") or ""))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Date Sold", "Description", "Card Name", "Condition",
        "Sale Price (£)", "Purchase Cost (£)", "Gross Profit (£)",
        "eBay Fees est. (£)", "Net Profit (£)", "Platform", "Source",
    ])
    total_revenue = total_cost = total_net = 0.0
    for item in filtered:
        sell  = float(item.get("sell_price") or 0)
        cost  = float(item.get("purchase_price") or 0)
        gross = sell - cost
        fees  = round(sell * 0.1235, 2)
        net   = round(gross - fees, 2)
        total_revenue += sell; total_cost += cost; total_net += net
        writer.writerow([
            str(item.get("date_sold") or "")[:10],
            f"Pokemon TCG - {item.get('card_name','Unknown')}",
            item.get("card_name", ""),
            item.get("condition", ""),
            f"{sell:.2f}", f"{cost:.2f}", f"{gross:.2f}",
            f"{fees:.2f}", f"{net:.2f}", "eBay UK",
            item.get("source", ""),
        ])
    writer.writerow([])
    writer.writerow(["TOTALS", "", "", "",
                     f"{total_revenue:.2f}", f"{total_cost:.2f}",
                     f"{total_revenue-total_cost:.2f}", "", f"{total_net:.2f}", "", ""])
    output.seek(0)
    label = f"{year or 'all'}{f'-{month:02d}' if month else ''}"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=pokemaz-{label}.csv"},
    )


@router.get("/export/xero")
async def export_xero_csv(year: int = None, month: int = None, user: dict = Depends(get_current_user)):
    """Xero-compatible sales invoice CSV import format."""
    all_sold = await db.get_all_items(user["id"], status_filter="Sold")
    filtered = sorted(_filter_by_period(all_sold, year, month),
                      key=lambda x: str(x.get("date_sold") or ""))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "*ContactName", "*InvoiceNumber", "*InvoiceDate", "*DueDate",
        "Description", "*Quantity", "*UnitAmount", "*AccountCode", "*TaxType",
    ])
    for item in filtered:
        sell      = float(item.get("sell_price") or 0)
        date_sold = str(item.get("date_sold") or "")[:10]
        writer.writerow([
            "eBay UK Buyer",
            f"INV-{item['item_id']}",
            date_sold,
            date_sold,
            f"Pokemon TCG - {item.get('card_name', '')}",
            "1",
            f"{sell:.2f}",
            "200",
            "20% (VAT on Income)" if sell > 0 else "No VAT",
        ])

    output.seek(0)
    filename = f"pokemaz-xero-{year or 'all'}{f'-{month:02d}' if month else ''}.csv"
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/quickbooks")
async def export_quickbooks_csv(year: int = None, month: int = None, user: dict = Depends(get_current_user)):
    """QuickBooks-compatible sales receipt CSV import format."""
    all_sold = await db.get_all_items(user["id"], status_filter="Sold")
    filtered = sorted(_filter_by_period(all_sold, year, month),
                      key=lambda x: str(x.get("date_sold") or ""))

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date", "Customer", "Item", "Description", "Quantity", "Rate", "Amount"])
    for item in filtered:
        sell      = float(item.get("sell_price") or 0)
        date_sold = str(item.get("date_sold") or "")[:10]
        writer.writerow([
            date_sold,
            "eBay Buyer",
            "Pokemon TCG Card",
            item.get("card_name", ""),
            "1",
            f"{sell:.2f}",
            f"{sell:.2f}",
        ])

    output.seek(0)
    filename = f"pokemaz-quickbooks-{year or 'all'}{f'-{month:02d}' if month else ''}.csv"
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/export/selected")
async def export_selected_csv(item_ids: str = "", user: dict = Depends(get_current_user)):
    ids = [int(x) for x in item_ids.split(",") if x.strip().isdigit()]
    all_items = await db.get_all_items(user["id"])
    filtered = [i for i in all_items if i["item_id"] in ids]

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Item ID", "Card Name", "Condition", "Region",
                     "Purchase Price (£)", "Market Price (£)", "Quick Price (£)",
                     "Sell Price (£)", "Status", "eBay Listed", "Date Added"])
    for item in filtered:
        writer.writerow([
            item["item_id"], item.get("card_name", ""), item.get("condition", ""),
            item.get("region", ""), item.get("purchase_price", ""),
            item.get("live_price", ""), item.get("quick_price", ""),
            item.get("sell_price", ""), item.get("status", ""),
            item.get("ebay_listed", ""), item.get("date_added", ""),
        ])
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=pokemaz-selected.csv"},
    )


@router.get("/forecast")
async def get_forecast(user: dict = Depends(get_current_user)):
    summary = await db.get_summary_stats(user["id"])
    sold    = await db.get_all_items(user["id"], status_filter="Sold")

    days_list = []
    for item in sold:
        date_added = item.get("date_added")
        date_sold  = item.get("date_sold")
        if not date_added or not date_sold:
            continue
        try:
            added   = datetime.strptime(str(date_added)[:10], "%Y-%m-%d")
            sold_dt = datetime.strptime(str(date_sold)[:10],  "%Y-%m-%d")
            days_list.append(max((sold_dt - added).days, 1))
        except ValueError:
            continue

    avg_days       = sum(days_list) / len(days_list) if days_list else 30.0
    est_items_30d  = round(30 / avg_days * summary["in_stock"]) if avg_days > 0 and summary["in_stock"] else 0
    est_profit_30d = round(est_items_30d * summary.get("avg_profit", 0), 2)

    return {
        "avg_days_to_sell": round(avg_days, 1),
        "est_items_30d":    est_items_30d,
        "est_profit_30d":   est_profit_30d,
        "in_stock":         summary["in_stock"],
    }


@router.get("/best-time")
async def get_best_time_to_sell(user: dict = Depends(get_current_user)):
    """Analyse which days of week sell best based on Date_Sold data."""
    all_sold = await db.get_all_items(user["id"], status_filter="Sold")

    days_of_week = {i: {"day": ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][i],
                         "count": 0, "revenue": 0.0, "profit": 0.0} for i in range(7)}

    for item in all_sold:
        sold_date = str(item.get("date_sold") or "")[:10]
        if not sold_date or sold_date == "2026-05-31":
            continue
        try:
            dt = datetime.strptime(sold_date, "%Y-%m-%d")
            dow = dt.weekday()
            sell = float(item.get("sell_price") or 0)
            cost = float(item.get("purchase_price") or 0)
            days_of_week[dow]["count"]   += 1
            days_of_week[dow]["revenue"] += sell
            days_of_week[dow]["profit"]  += sell - cost
        except Exception:
            continue

    result = list(days_of_week.values())
    for d in result:
        d["avg_profit"] = round(d["profit"] / d["count"], 2) if d["count"] else 0
        d["profit"]     = round(d["profit"], 2)
        d["revenue"]    = round(d["revenue"], 2)

    best_day = max(result, key=lambda x: x["count"])
    best_idx = result.index(best_day)
    list_day = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][(best_idx - 1) % 7]

    return {
        "by_day":         result,
        "best_day":       best_day["day"],
        "recommendation": f"List cards on {list_day} evening to maximise {best_day['day']} sales",
    }


@router.get("/concentration")
async def get_portfolio_concentration(user: dict = Depends(get_current_user)):
    """Show % of inventory value by set to identify concentration risk."""
    import re
    from collections import defaultdict

    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    sets = defaultdict(lambda: {"count": 0, "value": 0.0, "cost": 0.0})
    total_value = 0.0

    for item in all_items:
        name = item.get("card_name", "")
        m = re.search(r'\(Pokemon (.+?)\)', name)
        set_name = m.group(1) if m else "Unknown"
        live = float(item.get("live_price") or 0)
        cost = float(item.get("purchase_price") or 0)
        sets[set_name]["count"] += 1
        sets[set_name]["value"] += live
        sets[set_name]["cost"]  += cost
        total_value += live

    result = []
    for set_name, data in sets.items():
        pct = (data["value"] / total_value * 100) if total_value else 0
        result.append({
            "set":   set_name,
            "count": data["count"],
            "value": round(data["value"], 2),
            "cost":  round(data["cost"], 2),
            "pct":   round(pct, 1),
            "risk":  "high" if pct > 30 else "medium" if pct > 15 else "low",
        })

    result.sort(key=lambda x: x["value"], reverse=True)
    return {"sets": result, "total_value": round(total_value, 2)}


@router.get("/predictions")
async def get_price_predictions(user: dict = Depends(get_current_user)):
    """Linear regression on price history to predict 30-day trend direction."""
    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    predictions = []

    for item in all_items:
        history = await db.get_price_history(user["id"], item["item_id"], limit=30)
        prices = [h["live_price_gbp"] for h in history if h.get("live_price_gbp")]
        if len(prices) < 7:
            continue

        n = len(prices)
        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(prices) / n
        numerator   = sum((x[i] - x_mean) * (prices[i] - y_mean) for i in range(n))
        denominator = sum((x[i] - x_mean) ** 2 for i in range(n))
        slope = numerator / denominator if denominator else 0

        weekly_change_pct = (slope * 7 / y_mean * 100) if y_mean else 0
        if abs(weekly_change_pct) < 2:
            continue

        current_price  = prices[-1]
        predicted_30d  = current_price + (slope * 30)
        predictions.append({
            "item_id":        item["item_id"],
            "card_name":      item["card_name"],
            "current_price":  round(current_price, 2),
            "predicted_30d":  round(predicted_30d, 2),
            "weekly_change":  round(weekly_change_pct, 1),
            "trend":          "rising" if slope > 0 else "falling",
            "data_points":    n,
            "purchase_price": float(item.get("purchase_price") or 0),
        })

    predictions.sort(key=lambda x: abs(x["weekly_change"]), reverse=True)
    return {
        "rising":          [p for p in predictions if p["trend"] == "rising"][:10],
        "falling":         [p for p in predictions if p["trend"] == "falling"][:10],
        "total_analysed":  len(predictions),
    }


@router.get("/restock")
async def get_restock_web(user: dict = Depends(get_current_user)):
    suggestions = await db.get_restock_suggestions(user["id"])
    return {"suggestions": suggestions}


@router.get("/data-health")
async def get_data_health(user: dict = Depends(get_current_user)):
    """Identify inventory items with missing, zero, or stale price data."""
    all_items = await db.get_all_items(user["id"], status_filter="Inventory")
    now = datetime.now(timezone.utc)

    zero_price:  list[dict] = []
    no_pc_url:   list[dict] = []
    stale_price: list[dict] = []

    for item in all_items:
        live   = float(item.get("live_price") or 0)
        pc_url = item.get("pc_url", "")

        if not pc_url:
            no_pc_url.append(item)
            continue

        if live <= 0:
            zero_price.append(item)
            continue

        try:
            history = await db.get_price_history(user["id"], item["item_id"], limit=1)
            if history:
                last_ts = datetime.fromisoformat(str(history[-1]["timestamp"]))
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                if (now - last_ts).days > 14:
                    stale_price.append(item)
        except Exception:
            pass

    def slim(items):
        return [{"id": i["item_id"], "name": i["card_name"]} for i in items]

    return {
        "zero_price":  slim(zero_price),
        "no_pc_url":   slim(no_pc_url),
        "stale_price": slim(stale_price),
        "total":       len(zero_price) + len(no_pc_url) + len(stale_price),
    }
