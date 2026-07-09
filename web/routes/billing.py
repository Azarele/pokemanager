"""
Stripe billing routes for PokeManager subscriptions.
"""
import os
import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from web.auth import get_current_user
from web.database import get_db
from web.notifications import send_discord_notification

router = APIRouter()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

PLANS = {
    "gym_leader": {
        "name":        "Gym Leader",
        "price_id":    os.getenv("STRIPE_PRICE_GYM_LEADER", ""),
        "amount_gbp":  7.99,
        "description": "Unlimited inventory, eBay listing, AI descriptions & exports",
    },
    "champion": {
        "name":        "Champion",
        "price_id":    os.getenv("STRIPE_PRICE_CHAMPION", ""),
        "amount_gbp":  14.99,
        "description": "Everything in Gym Leader plus managed AI descriptions & priority support",
    },
}


@router.post("/create-checkout")
async def create_checkout(body: dict, user: dict = Depends(get_current_user)):
    plan = body.get("plan")
    if plan not in PLANS:
        raise HTTPException(400, "Invalid plan")

    db = get_db()
    app_url = os.getenv("APP_URL", "http://localhost:8000")

    # Get or create Stripe customer
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        customer = stripe.Customer.create(
            email=user["email"],
            metadata={"user_id": user["id"]},
        )
        customer_id = customer.id
        db.table("user_profiles").update(
            {"stripe_customer_id": customer_id}
        ).eq("id", user["id"]).execute()

    session = stripe.checkout.Session.create(
        customer=customer_id,
        payment_method_types=["card"],
        line_items=[{"price": PLANS[plan]["price_id"], "quantity": 1}],
        mode="subscription",
        subscription_data={
            "trial_period_days": 7,
            "metadata": {"user_id": user["id"], "plan": plan},
        },
        success_url=f"{app_url}/upgrade?success=true&plan={plan}",
        cancel_url=f"{app_url}/upgrade?cancelled=true",
        metadata={"user_id": user["id"], "plan": plan},
    )

    return {"checkout_url": session.url}


@router.post("/cancel")
async def cancel_subscription(user: dict = Depends(get_current_user)):
    sub_id = user.get("stripe_subscription_id")
    if not sub_id:
        return {"success": False, "error": "No active subscription"}

    try:
        stripe.Subscription.modify(
            sub_id,
            cancel_at_period_end=True,
        )
        return {"success": True, "message": "Subscription will cancel at end of billing period"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/portal")
async def billing_portal(user: dict = Depends(get_current_user)):
    customer_id = user.get("stripe_customer_id")
    if not customer_id:
        raise HTTPException(400, "No billing account found")

    app_url = os.getenv("APP_URL", "http://localhost:8000")
    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{app_url}/settings",
    )
    return RedirectResponse(session.url)


@router.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        raise HTTPException(400, f"Webhook error: {e}")

    db = get_db()
    event_type = event["type"]
    obj = event["data"]["object"]

    # Convert Stripe object to plain dict for safe access
    if hasattr(obj, 'to_dict'):
        data = obj.to_dict()
    else:
        import json
        data = json.loads(str(obj))

    print(f"[billing] Webhook: {event_type}")

    if event_type == "checkout.session.completed":
        metadata = data.get("metadata") or {}
        user_id  = metadata.get("user_id")
        plan     = metadata.get("plan")
        sub_id   = data.get("subscription")

        print(f"[billing] checkout.session.completed — user_id={user_id}, plan={plan}")

        if user_id and plan:
            db.table("user_profiles").update({
                "plan":                   plan,
                "stripe_subscription_id": sub_id,
                "subscription_status":    "active",
            }).eq("id", user_id).execute()
            print(f"[billing] ✅ Upgraded {user_id} to {plan}")
            plan_display = {"gym_leader": "Gym Leader", "champion": "Champion"}.get(plan, plan)
            await send_discord_notification(
                user_id,
                "⭐ Plan Upgraded",
                f"You're now on **{plan_display}**! Enjoy your new features.",
                5763719,
            )

    elif event_type == "customer.subscription.updated":
        customer_id = data.get("customer")
        status      = data.get("status")
        period_end  = data.get("current_period_end")
        items       = data.get("items", {})
        item_data   = items.get("data", []) if isinstance(items, dict) else []
        plan_id     = item_data[0].get("price", {}).get("id") if item_data else None

        plan = next((k for k, v in PLANS.items() if v["price_id"] == plan_id), None)

        profile = db.table("user_profiles").select("id").eq(
            "stripe_customer_id", customer_id
        ).single().execute()

        if profile.data:
            updates = {"subscription_status": status}
            if plan:
                updates["plan"] = plan
            if period_end:
                from datetime import datetime, timezone
                updates["subscription_period_end"] = datetime.fromtimestamp(
                    period_end, tz=timezone.utc
                ).isoformat()
            db.table("user_profiles").update(updates).eq(
                "id", profile.data["id"]
            ).execute()
            print(f"[billing] Updated subscription for {customer_id}: {updates}")

    elif event_type in ("customer.subscription.deleted", "customer.subscription.canceled"):
        customer_id = data.get("customer")

        profile = db.table("user_profiles").select("id").eq(
            "stripe_customer_id", customer_id
        ).single().execute()

        if profile.data:
            db.table("user_profiles").update({
                "plan":                   "free",
                "subscription_status":    "canceled",
                "stripe_subscription_id": None,
            }).eq("id", profile.data["id"]).execute()
            print(f"[billing] Downgraded {customer_id} to free")

    return {"received": True}
