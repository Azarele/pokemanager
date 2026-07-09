from fastapi import APIRouter, Depends

from web import db_inventory as db
from web.auth import get_current_user

router = APIRouter()

_LIMIT_DEFAULT = 90


@router.get("/summary/{item_id}")
async def get_price_summary(item_id: int, user: dict = Depends(get_current_user)):
    try:
        history = await db.get_price_history(user["id"], item_id, limit=_LIMIT_DEFAULT)
    except Exception as e:
        print(f"[price_history] Error reading history for {item_id}: {e}")
        return {"item_id": item_id, "trend": "unknown", "history": [], "error": str(e)}
    if not history:
        return {"item_id": item_id, "trend": "unknown", "history": []}

    prices  = [h["live_price_gbp"] for h in history if h.get("live_price_gbp") is not None]
    if not prices:
        return {"item_id": item_id, "trend": "unknown", "history": history}

    current = prices[-1]
    ago_30  = prices[-30] if len(prices) >= 30 else prices[0]
    ago_90  = prices[0]

    change_30d = round(((current - ago_30) / ago_30 * 100) if ago_30 else 0, 1)
    if change_30d > 5:
        trend = "rising"
    elif change_30d < -5:
        trend = "falling"
    else:
        trend = "stable"

    return {
        "item_id":    item_id,
        "current":    current,
        "ago_30d":    ago_30,
        "ago_90d":    ago_90,
        "change_30d": change_30d,
        "trend":      trend,
        "history":    history,
    }


@router.get("/{item_id}")
async def get_price_history(item_id: int, days: int = _LIMIT_DEFAULT, user: dict = Depends(get_current_user)):
    try:
        history = await db.get_price_history(user["id"], item_id, limit=days)
    except Exception as e:
        print(f"[price_history] Error reading history for {item_id}: {e}")
        return {"item_id": item_id, "history": [], "count": 0, "error": str(e)}
    return {"item_id": item_id, "history": history, "count": len(history)}
