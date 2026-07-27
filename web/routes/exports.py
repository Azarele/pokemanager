"""
Export routes for PokeManager.
Handles CSV and other data exports for users.
"""
import logging
import io
from datetime import datetime
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
import csv

from web.auth import get_current_user
from web.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/csv")
async def export_csv(user: dict = Depends(get_current_user)):
    """
    Export all sold items as CSV file.
    Returns: date_sold, card_name, condition, source, purchase_price, sell_price, ebay_fee, profit, roi_pct, ebay_order_id
    """
    try:
        db = get_db()

        # Fetch all sold items for current user
        result = db.table("inventory_items").select(
            "date_sold, card_name, condition, source, purchase_price, sell_price, "
            "ebay_fee, profit, roi_pct, ebay_order_id"
        ).eq("user_id", user["id"]).eq("status", "Sold").order("date_sold", desc=True).execute()

        sold_items = result.data or []

        # Create CSV in memory
        output = io.StringIO()
        writer = csv.writer(output)

        # Write header
        writer.writerow([
            "Date Sold", "Card Name", "Condition", "Source",
            "Purchase Price", "Sell Price", "eBay Fee", "Profit", "ROI %", "Order ID"
        ])

        # Write data rows
        for item in sold_items:
            writer.writerow([
                item.get("date_sold", ""),
                item.get("card_name", ""),
                item.get("condition", ""),
                item.get("source", ""),
                f"£{item.get('purchase_price', 0):.2f}",
                f"£{item.get('sell_price', 0):.2f}",
                f"£{item.get('ebay_fee', 0):.2f}",
                f"£{item.get('profit', 0):.2f}",
                f"{item.get('roi_pct', 0):.1f}%",
                item.get("ebay_order_id", ""),
            ])

        # Create streaming response
        output.seek(0)
        filename = f"pokemanager-sales-{datetime.now().strftime('%Y%m%d')}.csv"

        logger.info(f"[export] User {user['id']} exported {len(sold_items)} sold items to CSV")

        return StreamingResponse(
            iter([output.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        logger.error(f"[export] CSV export failed for user {user['id']}: {e}", exc_info=True)
        return {"success": False, "error": str(e)}
