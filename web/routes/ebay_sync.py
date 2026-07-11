"""
eBay sales sync — auto-detect completed sales and offers, send Discord notifications.
"""
import asyncio
import base64
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException

from web.auth import get_current_user
from web.database import get_db
from web import db_inventory as db
from web.notifications import send_discord_notification

import config

router = APIRouter()

# Per-user token cache: {user_id: {"token": "...", "expiry": unix_timestamp}}
_token_cache: dict[str, dict] = {}


async def _get_user_ebay_token(user_id: str) -> str:
    """Get or refresh eBay access token for a user."""
    global _token_cache

    # Check if cached token is still valid (5 min buffer)
    if user_id in _token_cache:
        entry = _token_cache[user_id]
        if time.time() < entry["expiry"] - 300:
            return entry["token"]

    # Get user's eBay credentials
    db_client = get_db()
    profile = db_client.table("user_profiles").select(
        "ebay_app_id", "ebay_cert_id", "ebay_refresh_token"
    ).eq("id", user_id).single().execute()

    if not profile.data:
        raise ValueError(f"User {user_id} not found")

    user_profile = profile.data
    app_id = user_profile.get("ebay_app_id")
    cert_id = user_profile.get("ebay_cert_id")
    refresh_token = user_profile.get("ebay_refresh_token")

    if not all([app_id, cert_id, refresh_token]):
        raise ValueError("User does not have eBay credentials configured")

    # Exchange refresh token for access token
    credentials = base64.b64encode(f"{app_id}:{cert_id}".encode()).decode()
    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "scope": " ".join([
                "https://api.ebay.com/oauth/api_scope/sell.inventory",
                "https://api.ebay.com/oauth/api_scope/sell.account",
                "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
            ]),
        },
        timeout=30,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"eBay token refresh failed: {resp.text}")

    data = resp.json()
    access_token = data["access_token"]
    expires_in = data.get("expires_in", 7200)

    _token_cache[user_id] = {
        "token": access_token,
        "expiry": time.time() + expires_in,
    }

    return access_token


def _get_ebay_headers(access_token: str) -> dict:
    """Build headers for eBay REST API calls."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


async def _get_recent_orders(
    user_id: str,
    access_token: str,
    days_back: int = 7,
) -> list[dict]:
    """
    Fetch recently completed orders from eBay Fulfillment API.

    Args:
        user_id: User's Supabase ID (for logging)
        access_token: eBay OAuth access token
        days_back: How many days back to check (default 7)

    Returns:
        List of order dicts with orderId, orderLineItems[], etc.
    """
    cutoff = (datetime.utcnow() - timedelta(days=days_back)).isoformat() + "Z"

    try:
        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers=_get_ebay_headers(access_token),
            params={
                "filter": f"orderstatus:{{'COMPLETED'}}",
                "sort": "lastModifiedDate:desc",
                "limit": 100,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[ebay_sync] get_recent_orders HTTP {resp.status_code}: {resp.text}")
            return []

        data = resp.json()
        orders = data.get("orders", [])
        print(f"[ebay_sync] Found {len(orders)} completed orders for user {user_id}")
        return orders
    except Exception as e:
        print(f"[ebay_sync] Error fetching orders: {e}")
        return []


async def _get_pending_offers(
    user_id: str,
    access_token: str,
) -> list[dict]:
    """
    Fetch pending offers from eBay Inventory API.

    Args:
        user_id: User's Supabase ID (for logging)
        access_token: eBay OAuth access token

    Returns:
        List of offer dicts with offerId, listingId, pricingSummary, etc.
    """
    try:
        resp = requests.get(
            "https://api.ebay.com/sell/inventory/v1/offers",
            headers=_get_ebay_headers(access_token),
            params={
                "filter": "status:ACTIVE",
                "limit": 100,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[ebay_sync] get_pending_offers HTTP {resp.status_code}: {resp.text}")
            return []

        data = resp.json()
        offers = data.get("offers", [])
        print(f"[ebay_sync] Found {len(offers)} active offers for user {user_id}")
        return offers
    except Exception as e:
        print(f"[ebay_sync] Error fetching offers: {e}")
        return []


async def _sync_user_sales(user_id: str) -> dict:
    """
    Sync completed sales for a user from eBay.

    Returns:
        {
            "synced": int,          # Number of items auto-marked as sold
            "skipped": int,         # Already marked sold
            "errors": int,
            "offers_found": int,
        }
    """
    stats = {"synced": 0, "skipped": 0, "errors": 0, "offers_found": 0}

    try:
        access_token = await _get_user_ebay_token(user_id)
    except Exception as e:
        print(f"[ebay_sync] Could not get token for user {user_id}: {e}")
        return stats

    # Get user's settings and inventory once
    db_client = get_db()
    user_profile = db_client.table("user_profiles").select(
        "ebay_fee_rate", "postage_cost"
    ).eq("id", user_id).single().execute()

    user_data = user_profile.data or {}
    ebay_fee_rate = float(user_data.get("ebay_fee_rate") or 0.1235)
    postage_cost = float(user_data.get("postage_cost") or 1.50)

    # Fetch all inventory items once for this user
    inventory_items = await db.get_all_items(user_id)
    inventory_by_id = {i["item_id"]: i for i in inventory_items}

    # Fetch orders and sync them
    orders = await _get_recent_orders(user_id, access_token)
    for order in orders:
        try:
            order_id = order.get("orderId")
            line_items = order.get("lineItems", [])
            order_creation_date = order.get("creationDate", "")[:10]

            for item in line_items:
                sku = item.get("sku", "")
                if not sku:
                    continue

                # SKU format is typically item_id
                try:
                    item_id = int(sku)
                except (ValueError, TypeError):
                    continue

                # Check if item exists and is not already sold
                inv_item = inventory_by_id.get(item_id)
                if not inv_item:
                    continue

                if inv_item.get("status") == "Sold":
                    stats["skipped"] += 1
                    continue

                # Extract order data
                price_paid = float(item.get("lineItemCost", 0))
                ebay_listing_id = item.get("legacyItemId", "")

                # Mark as sold with order info
                await db.edit_item(user_id, item_id, "status", "Sold")
                await db.edit_item(user_id, item_id, "date_sold", order_creation_date)
                await db.edit_item(user_id, item_id, "sell_price", price_paid)
                if ebay_listing_id:
                    await db.edit_item(user_id, item_id, "ebay_listing_id", ebay_listing_id)

                # Send Discord notification
                purchase_price = float(inv_item.get("purchase_price") or 0)
                profit_emoji = "📈" if price_paid > purchase_price else "📉"
                profit = price_paid - purchase_price
                roi = (profit / purchase_price * 100) if purchase_price > 0 else 0

                await send_discord_notification(
                    user_id,
                    "🎉 Item Sold on eBay!",
                    (
                        f"**{inv_item.get('card_name')}**\n"
                        f"💰 Sold for: **£{price_paid:.2f}**\n"
                        f"📦 Bought for: £{purchase_price:.2f}\n"
                        f"{profit_emoji} Profit: **£{profit:.2f}** ({roi:.1f}% ROI)\n"
                        f"🏷️ eBay Order #{order_id}"
                    ),
                    5763719,  # green
                )

                stats["synced"] += 1
                print(f"[ebay_sync] Synced item {item_id} for user {user_id}")

        except Exception as e:
            stats["errors"] += 1
            print(f"[ebay_sync] Error processing order {order.get('orderId')}: {e}")

    # Fetch pending offers (don't auto-process, just notify)
    offers = await _get_pending_offers(user_id, access_token)
    stats["offers_found"] = len(offers)

    return stats


@router.post("/sync-sales")
async def sync_ebay_sales(user: dict = Depends(get_current_user)):
    """
    Manually trigger eBay sales sync for current user.
    Fetches completed orders, marks items as sold, sends Discord notifications.
    """
    if not user.get("ebay_refresh_token"):
        raise HTTPException(status_code=400, detail="eBay credentials not configured")

    stats = await _sync_user_sales(user["id"])
    return {
        "success": True,
        "synced": stats["synced"],
        "skipped": stats["skipped"],
        "errors": stats["errors"],
        "offers_found": stats["offers_found"],
    }


@router.post("/check-offers")
async def check_ebay_offers(user: dict = Depends(get_current_user)):
    """
    Check for pending offers and send Discord notifications with ROI analysis.
    """
    if not user.get("ebay_refresh_token"):
        raise HTTPException(status_code=400, detail="eBay credentials not configured")

    try:
        access_token = await _get_user_ebay_token(user["id"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Get user's settings
    db_client = get_db()
    user_profile = db_client.table("user_profiles").select(
        "ebay_fee_rate", "postage_cost"
    ).eq("id", user["id"]).single().execute()

    user_data = user_profile.data or {}
    ebay_fee_rate = float(user_data.get("ebay_fee_rate") or 0.1235)
    postage_cost = float(user_data.get("postage_cost") or 1.50)

    # Fetch inventory items once
    inventory_items = await db.get_all_items(user["id"])
    inventory_by_listing_id = {i.get("ebay_listing_id"): i for i in inventory_items if i.get("ebay_listing_id")}

    # Fetch offers
    offers = await _get_pending_offers(user["id"], access_token)

    offer_notifications = []
    for offer in offers:
        try:
            offer_id = offer.get("offerId")
            listing_id = offer.get("listingId")
            offer_price = float(offer.get("pricingSummary", {}).get("price", {}).get("value", 0))

            if not offer_price:
                continue

            # Find matching inventory item by eBay listing ID
            inv_item = inventory_by_listing_id.get(str(listing_id))
            if not inv_item:
                continue

            purchase_price = float(inv_item.get("purchase_price") or 0)

            # Calculate profit/loss after fees and postage
            ebay_fee = round(offer_price * ebay_fee_rate, 2)
            net_received = round(offer_price - ebay_fee - postage_cost, 2)
            profit = round(net_received - purchase_price, 2)
            roi = (profit / purchase_price * 100) if purchase_price > 0 else 0

            # Recommendation logic
            if roi >= 10:
                recommendation = "✅ Accept"
                color = 5763719  # green
            elif roi >= 0:
                recommendation = "⚠️ Counter"
                color = 16776960  # yellow
            else:
                recommendation = "❌ Decline"
                color = 15548997  # red

            # Market value (from live_price if available)
            market_price = float(inv_item.get("live_price") or purchase_price)
            offer_vs_market = ((offer_price - market_price) / market_price * 100) if market_price > 0 else 0
            offer_comparison = "above" if offer_vs_market > 0 else "below"

            message = (
                f"**{inv_item.get('card_name')}** (#{listing_id})\n"
                f"📨 Offer: **£{offer_price:.2f}**\n"
                f"📊 Analysis:\n"
                f"  • Net after fees: £{net_received:.2f}\n"
                f"  • Profit: {'+' if profit >= 0 else ''}£{profit:.2f} ({roi:.1f}% ROI)\n"
                f"  • Market value: £{market_price:.2f}\n"
                f"  • Offer is {abs(offer_vs_market):.0f}% {offer_comparison} market\n"
                f"{recommendation}"
            )

            await send_discord_notification(
                user["id"],
                "💬 New Offer Received!",
                message,
                color,
            )

            offer_notifications.append({
                "offer_id": offer_id,
                "listing_id": listing_id,
                "price": offer_price,
                "recommendation": recommendation,
            })

            print(f"[ebay_sync] Sent offer notification for listing {listing_id}")

        except Exception as e:
            print(f"[ebay_sync] Error processing offer: {e}")

    return {
        "success": True,
        "offers_checked": len(offers),
        "notifications_sent": len(offer_notifications),
    }


async def sync_all_users_ebay():
    """
    Background task: sync all users who have eBay credentials configured.
    Called every 30 minutes.
    """
    try:
        db_client = get_db()
        profiles = db_client.table("user_profiles").select("id").neq(
            "ebay_refresh_token", None
        ).execute()

        user_ids = [p["id"] for p in (profiles.data or [])]
        print(f"[ebay_sync] Starting sync for {len(user_ids)} users")

        for user_id in user_ids:
            try:
                stats = await _sync_user_sales(user_id)
                print(f"[ebay_sync] User {user_id}: synced={stats['synced']}, skipped={stats['skipped']}, errors={stats['errors']}")
            except Exception as e:
                print(f"[ebay_sync] Error syncing user {user_id}: {e}")

        print("[ebay_sync] Background sync complete")
    except Exception as e:
        print(f"[ebay_sync] Background sync failed: {e}")
