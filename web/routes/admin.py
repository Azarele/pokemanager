"""
Admin routes — only accessible to users with role='admin'.
Provides business analytics, user management, and Stripe revenue data.
"""
import os
import stripe
from fastapi import APIRouter, Depends, HTTPException
from web.auth import get_current_user
from web.database import get_db

router = APIRouter()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")


def require_admin(user: dict = Depends(get_current_user)):
    if user.get("role") != "admin":
        raise HTTPException(403, "Admin access required")
    return user


@router.get("/overview")
async def admin_overview(admin: dict = Depends(require_admin)):
    db = get_db()

    profiles = db.table("user_profiles").select(
        "id, email, plan, role, created_at, subscription_status, stripe_customer_id"
    ).execute()
    users = profiles.data or []

    total        = len(users)
    free_users   = [u for u in users if (u.get("plan") or "free") == "free" and u.get("role") != "admin"]
    gym_leaders  = [u for u in users if u.get("plan") == "gym_leader" and u.get("role") != "admin"]
    champions    = [u for u in users if u.get("plan") == "champion" and u.get("role") != "admin"]
    admins       = [u for u in users if u.get("role") == "admin"]
    paying       = [u for u in users if u.get("plan") in ("gym_leader", "champion")]

    mrr = len(gym_leaders) * 7.99 + len(champions) * 14.99
    arr = mrr * 12

    # New users this month
    from datetime import date
    this_month = date.today().strftime("%Y-%m")
    new_this_month = [
        u for u in users
        if str(u.get("created_at") or "").startswith(this_month)
    ]

    # Total items across all users
    items_result = db.table("inventory_items").select(
        "id", count="exact"
    ).execute()
    total_items = items_result.count or 0

    return {
        "users": {
            "total":          total,
            "free":           len(free_users),
            "gym_leader":     len(gym_leaders),
            "champion":       len(champions),
            "admin":          len(admins),
            "paying":         len(paying),
            "new_this_month": len(new_this_month),
        },
        "revenue": {
            "mrr":            round(mrr, 2),
            "arr":            round(arr, 2),
            "avg_revenue_per_user": round(mrr / len(paying), 2) if paying else 0,
        },
        "platform": {
            "total_items": total_items,
        },
    }


@router.get("/users")
async def list_users(
    admin: dict = Depends(require_admin),
    limit: int = 100,
    offset: int = 0,
    plan: str = None,
):
    db = get_db()

    query = db.table("user_profiles").select(
        "id, email, display_name, plan, role, created_at, subscription_status, subscription_period_end, stripe_customer_id"
    )

    if plan and plan != "":
        query = query.eq("plan", plan)

    result = query.order("created_at", desc=True).execute()
    users = result.data or []

    # Add item counts
    for u in users:
        items = db.table("inventory_items").select("id", count="exact")\
            .eq("user_id", u["id"]).execute()
        u["item_count"] = items.count or 0

    return {"users": users, "total": len(users)}


@router.get("/users/{user_id}")
async def get_user_detail(user_id: str, admin: dict = Depends(require_admin)):
    db = get_db()

    profile = db.table("user_profiles").select("*").eq("id", user_id).single().execute()
    if not profile.data:
        raise HTTPException(404, "User not found")

    user = profile.data

    # Item stats
    items = db.table("inventory_items").select(
        "status, purchase_price, sell_price, profit"
    ).eq("user_id", user_id).execute()

    all_items    = items.data or []
    inventory    = [i for i in all_items if i["status"] == "Inventory"]
    sold         = [i for i in all_items if i["status"] == "Sold"]
    total_profit = sum(float(i.get("profit") or 0) for i in sold)
    total_cost   = sum(float(i.get("purchase_price") or 0) for i in inventory)

    return {
        "profile": user,
        "stats": {
            "total_items":   len(all_items),
            "in_inventory":  len(inventory),
            "sold":          len(sold),
            "total_profit":  round(total_profit, 2),
            "cost_in_stock": round(total_cost, 2),
        },
    }


@router.patch("/users/{user_id}")
async def update_user(
    user_id: str,
    body: dict,
    admin: dict = Depends(require_admin),
):
    db = get_db()

    allowed = {"plan", "role", "subscription_status"}
    updates = {k: v for k, v in body.items() if k in allowed}

    if not updates:
        return {"success": False, "error": "Nothing to update"}

    # Prevent removing own admin role
    if user_id == admin["id"] and updates.get("role") != "admin":
        return {"success": False, "error": "Cannot remove your own admin role"}

    db.table("user_profiles").update(updates).eq("id", user_id).execute()

    return {"success": True, "updated": updates}


@router.get("/revenue")
async def get_revenue(admin: dict = Depends(require_admin)):
    if not os.getenv("STRIPE_SECRET_KEY", "").startswith("sk_"):
        return {
            "configured": False,
            "message": "Stripe not configured — add STRIPE_SECRET_KEY to .env",
        }

    try:
        # Last 6 months of charges
        import time
        six_months_ago = int(time.time()) - (180 * 86400)

        charges = stripe.Charge.list(
            limit=100,
            created={"gte": six_months_ago},
        )

        monthly = {}
        total_revenue = 0

        for charge in charges.auto_paging_iter():
            if charge.status != "succeeded":
                continue
            amount = charge.amount / 100  # pence to pounds
            month  = charge.created
            from datetime import datetime, timezone
            dt = datetime.fromtimestamp(month, tz=timezone.utc)
            key = dt.strftime("%Y-%m")
            monthly[key] = monthly.get(key, 0) + amount
            total_revenue += amount

        # Get active subscriptions count
        subs = stripe.Subscription.list(status="active", limit=100)
        active_subs = subs.total_count if hasattr(subs, 'total_count') else len(subs.data)

        return {
            "configured":     True,
            "total_revenue":  round(total_revenue, 2),
            "monthly":        [
                {"month": k, "revenue": round(v, 2)}
                for k, v in sorted(monthly.items())
            ],
            "active_subs":    active_subs,
        }

    except Exception as e:
        return {"configured": True, "error": str(e), "monthly": []}
