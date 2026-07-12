"""
eBay sales sync — auto-detect completed sales and offers, send Discord notifications.
"""
import asyncio
import base64
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Optional
import re

import requests
import httpx
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
    try:
        # Fetch most recent orders — eBay returns sorted by lastModifiedDate:desc by default
        # Already-sold items are filtered out in the sync logic anyway
        params = {
            "limit": 100,
        }
        headers = _get_ebay_headers(access_token)

        print(f"[ebay_sync] Querying most recent {params['limit']} orders for user {user_id}")

        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers=headers,
            params=params,
            timeout=30,
        )

        if resp.status_code != 200:
            print(f"[ebay_sync] get_recent_orders HTTP {resp.status_code}: {resp.text}")
            return []

        data = resp.json()
        orders = data.get("orders", [])
        print(f"[ebay_sync] Found {len(orders)} orders for user {user_id}")
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
                "limit": 100,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            # 404 or other errors might mean no offers or insufficient permissions
            if resp.status_code == 404:
                print(f"[ebay_sync] No active offers found or endpoint not available (HTTP 404)")
            else:
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
        "ebay_fee_rate"
    ).eq("id", user_id).single().execute()

    user_data = user_profile.data or {}
    ebay_fee_rate = float(user_data.get("ebay_fee_rate") or 0.1235)
    # Postage is always £0 for eBay sales - buyer pays via Simple Delivery

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

                # SKU format is "pokemaz-{item_id}" — extract the number
                # Skip bundles (pokemaz-bundle-*) as they represent multiple items
                if "-bundle-" in sku:
                    print(f"[ebay_sync] Skipping bundle SKU: {sku}")
                    continue

                try:
                    if sku.startswith("pokemaz-"):
                        item_id = int(sku.split("-", 1)[1])
                    else:
                        item_id = int(sku)
                except (ValueError, TypeError, IndexError):
                    print(f"[ebay_sync] Could not parse SKU: {sku}")
                    continue

                # Check if item exists and is not already sold
                inv_item = inventory_by_id.get(item_id)
                ebay_listing_id = item.get("legacyItemId", "")

                # Fallback: try matching by eBay listing_id if SKU-based lookup failed
                if not inv_item and ebay_listing_id:
                    print(f"[ebay_sync] SKU mismatch for {sku}, attempting fallback match by listing_id {ebay_listing_id}")
                    inv_item = await db.get_item_by_listing_id(user_id, ebay_listing_id)
                    if inv_item:
                        print(f"[ebay_sync] ✓ Matched by listing_id {ebay_listing_id} to item {inv_item.get('item_id')}")
                        item_id = inv_item.get("item_id")

                if not inv_item:
                    print(f"[ebay_sync] No inventory item found for SKU: {sku}, listing_id: {ebay_listing_id}")
                    continue

                already_sold = inv_item.get("status") == "Sold"
                print(f"[ebay_sync] Checking order {order_id}, item {item_id}, listing_id {ebay_listing_id}, status {inv_item.get('status')}")

                if already_sold:
                    print(f"[ebay_sync] Item already sold: {already_sold}")
                    stats["skipped"] += 1
                    continue

                # Extract order data - lineItemCost is a dict with value/currency
                cost_obj = item.get("lineItemCost", {})
                if isinstance(cost_obj, dict):
                    price_paid = float(cost_obj.get("value", 0))
                else:
                    price_paid = float(cost_obj or 0)

                # Extract purchase price early (used in profit calculation below)
                purchase_price = float(inv_item.get("purchase_price") or 0)

                # Using actual eBay order earnings, not estimated fees
                # eBay provides actual seller earnings in the fulfillment API
                # If promotions were applied, the lineItemCost reflects the final price after discounts
                # Extract actual fees from order-level charges
                ebay_fee = 0.0
                ebay_charges = order.get("ebayCollectedCharges", {})
                if isinstance(ebay_charges, dict):
                    for charge in ebay_charges.get("charges", []):
                        if isinstance(charge, dict):
                            amount_obj = charge.get("amount", {})
                            if isinstance(amount_obj, dict):
                                ebay_fee += float(amount_obj.get("value", 0))

                # If no fee data available, estimate based on item's promotion settings or flat rate
                if ebay_fee == 0 and price_paid > 0:
                    # Check if item has promoted listing percentage set
                    if inv_item.get("promoted_listing_pct"):
                        ebay_fee = round(price_paid * (float(inv_item.get("promoted_listing_pct")) / 100), 2)
                    else:
                        ebay_fee = round(price_paid * ebay_fee_rate, 2)
                else:
                    ebay_fee = round(ebay_fee / max(len(line_items), 1), 2) if ebay_charges else 0

                # Calculate profit: sale price - purchase price - actual ebay fees
                # Buyer pays postage via eBay Simple Delivery (we don't deduct postage)
                net_received = round(price_paid - ebay_fee, 2)
                profit = round(net_received - purchase_price, 2)

                # Log actual fee breakdown for verification
                print(f"[ebay_sync] Item {item_id}: price={price_paid}, fee={ebay_fee}, net={net_received}, profit={profit}")

                # Mark as sold with order info and calculated profit
                await db.edit_item(user_id, item_id, "status", "Sold")
                await db.edit_item(user_id, item_id, "date_sold", order_creation_date)
                await db.edit_item(user_id, item_id, "sell_price", price_paid)
                await db.edit_item(user_id, item_id, "profit", profit)
                if ebay_listing_id:
                    await db.edit_item(user_id, item_id, "ebay_listing_id", ebay_listing_id)

                # Send Discord notification
                profit_emoji = "📈" if profit > 0 else "📉"
                roi = (profit / purchase_price * 100) if purchase_price > 0 else 0

                await send_discord_notification(
                    user_id,
                    "🎉 Item Sold on eBay!",
                    (
                        f"**{inv_item.get('card_name')}**\n"
                        f"💰 Sold for: **£{price_paid:.2f}**\n"
                        f"📦 Bought for: £{purchase_price:.2f}\n"
                        f"💳 eBay fees: £{ebay_fee:.2f}\n"
                        f"📊 After fees: £{net_received:.2f}\n"
                        f"{profit_emoji} Profit: **£{profit:.2f}** ({roi:.1f}% ROI)\n"
                        f"🏷️ eBay Order #{order_id}"
                    ),
                    5763719 if profit > 0 else 15548997,  # green if profitable, red if loss
                )

                stats["synced"] += 1
                print(f"[ebay_sync] Successfully synced item {item_id} (profit: £{profit}) for user {user_id}")
                print(f"[ebay_sync] Found inventory match: {inv_item}")

        except Exception as e:
            stats["errors"] += 1
            print(f"[ebay_sync] Error processing order {order.get('orderId')}: {e}")

    # Fetch pending offers (don't auto-process, just notify)
    offers = await _get_pending_offers(user_id, access_token)
    stats["offers_found"] = len(offers)

    return stats


async def _check_best_offers(user_id: str, ebay_token: str) -> dict:
    """
    Check for pending Best Offer submissions using eBay Trading API.
    Returns stats on checked/notified offers.
    """
    stats = {"checked": 0, "notified": 0, "errors": 0}

    db_client = get_db()

    # Get user inventory and settings
    inventory_items = await db.get_all_items(user_id)
    inventory_by_listing = {str(i.get("ebay_listing_id")): i for i in inventory_items if i.get("ebay_listing_id")}

    user_profile = db_client.table("user_profiles").select(
        "ebay_fee_rate", "postage_cost"
    ).eq("id", user_id).single().execute()

    user_data = user_profile.data or {}
    ebay_fee_rate = float(user_data.get("ebay_fee_rate") or 0.1235)
    postage_cost = float(user_data.get("postage_cost") or 1.50)

    # Get Discord webhook for this user
    discord_webhook = await _get_user_discord_webhook(user_id)
    if not discord_webhook:
        return stats

    try:
        # Call eBay Trading API GetBestOffers
        headers = {
            "X-EBAY-API-SITEID": "3",  # UK
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "X-EBAY-API-CALL-NAME": "GetBestOffers",
            "X-EBAY-API-IAF-TOKEN": ebay_token,
            "Content-Type": "text/xml",
        }

        xml_body = '''<?xml version="1.0" encoding="utf-8"?>
<GetBestOffersRequest xmlns="urn:ebay:apis:eBLBaseComponents">
    <RequesterCredentials>
        <eBayAuthToken>{token}</eBayAuthToken>
    </RequesterCredentials>
    <BestOfferStatus>Pending</BestOfferStatus>
    <Pagination>
        <EntriesPerPage>20</EntriesPerPage>
    </Pagination>
</GetBestOffersRequest>'''.format(token=ebay_token)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.ebay.com/ws/api.dll",
                headers=headers,
                content=xml_body,
                timeout=30,
            )

        if response.status_code != 200:
            print(f"[ebay_sync] GetBestOffers error: {response.status_code} {response.text}")
            return stats

        # Parse XML response
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError as e:
            print(f"[ebay_sync] XML parse error: {e}")
            return stats

        # Extract offers from XML
        ns = {"ebay": "urn:ebay:apis:eBLBaseComponents"}
        best_offer_list = root.findall(".//ebay:BestOffer", ns)

        print(f"[ebay_sync] Found {len(best_offer_list)} pending best offers for user {user_id}")

        for offer in best_offer_list:
            try:
                offer_id = offer.findtext("ebay:BestOfferID", namespaces=ns) or ""
                item_id = offer.findtext("ebay:ItemID", namespaces=ns) or ""

                # Check if already notified
                existing = db_client.table("ebay_offers").select("id").eq(
                    "offer_id", offer_id
                ).eq("user_id", user_id).execute()

                if existing.data:
                    print(f"[ebay_sync] Already notified for offer {offer_id}")
                    continue

                # Get offer price
                price_elem = offer.find(".//ebay:Price", ns)
                if price_elem is not None:
                    offer_price = float(price_elem.text or 0)
                else:
                    continue

                # Find matching inventory item
                inv_item = inventory_by_listing.get(str(item_id))
                if not inv_item:
                    print(f"[ebay_sync] No inventory item found for listing {item_id}")
                    continue

                # Calculate ROI
                purchase_price = float(inv_item.get("purchase_price") or 0)
                market_price = float(inv_item.get("live_price") or purchase_price)

                ebay_fee = round(offer_price * ebay_fee_rate, 2)
                net_received = round(offer_price - ebay_fee - postage_cost, 2)
                profit = round(net_received - purchase_price, 2)
                roi = (profit / purchase_price * 100) if purchase_price > 0 else 0
                vs_market = ((offer_price - market_price) / market_price * 100) if market_price > 0 else 0

                # Determine recommendation
                if roi >= 15:
                    recommendation = "✅ Accept"
                    color = 5763719  # green
                elif roi >= 0:
                    recommendation = "⚠️ Counter"
                    color = 16776960  # yellow
                else:
                    recommendation = "❌ Decline"
                    color = 15548997  # red

                # Send Discord notification
                embed = {
                    "embeds": [{
                        "title": "💬 New Best Offer Received!",
                        "description": (
                            f"**{inv_item.get('card_name')}** (#{item_id})\n\n"
                            f"📨 **Offer: £{offer_price:.2f}**\n"
                            f"📊 **Analysis:**\n"
                            f"• Net after fees & postage: £{net_received:.2f}\n"
                            f"• Profit: {'+'if profit>=0 else ''}£{profit:.2f}\n"
                            f"• ROI: {'+'if roi>=0 else ''}{roi:.1f}%\n"
                            f"• Market value: £{market_price:.2f}\n"
                            f"• Offer is {abs(vs_market):.1f}% {'above' if vs_market>=0 else 'below'} market\n\n"
                            f"💡 **Recommendation: {recommendation}**"
                        ),
                        "color": color,
                        "footer": {"text": "PokeManager • Reply on eBay to respond to offer"},
                        "timestamp": datetime.utcnow().isoformat(),
                    }]
                }

                async with httpx.AsyncClient() as client:
                    await client.post(discord_webhook, json=embed)

                # Record notification
                db_client.table("ebay_offers").insert({
                    "user_id": user_id,
                    "offer_id": offer_id,
                    "item_id": str(item_id),
                    "inventory_item_id": inv_item.get("item_id"),
                    "offer_price": float(offer_price),
                    "status": "pending",
                }).execute()

                stats["notified"] += 1
                print(f"[ebay_sync] Sent best offer notification for item {item_id}: £{offer_price}")

            except Exception as e:
                stats["errors"] += 1
                print(f"[ebay_sync] Error processing best offer: {e}")

        stats["checked"] = len(best_offer_list)
        return stats

    except Exception as e:
        print(f"[ebay_sync] Error checking best offers: {e}")
        return stats


async def _get_user_discord_webhook(user_id: str) -> Optional[str]:
    """Get Discord webhook URL from user profile."""
    try:
        db_client = get_db()
        profile = db_client.table("user_profiles").select(
            "discord_webhook_url"
        ).eq("id", user_id).single().execute()
        return (profile.data or {}).get("discord_webhook_url")
    except Exception:
        return None


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
                # Sync completed sales
                stats = await _sync_user_sales(user_id)
                print(f"[ebay_sync] User {user_id}: synced={stats['synced']}, skipped={stats['skipped']}, errors={stats['errors']}")

                # Check for pending best offers
                try:
                    access_token = await _get_user_ebay_token(user_id)
                    offer_stats = await _check_best_offers(user_id, access_token)
                    print(f"[ebay_sync] User {user_id}: best_offers checked={offer_stats['checked']}, notified={offer_stats['notified']}, errors={offer_stats['errors']}")
                except Exception as e:
                    print(f"[ebay_sync] Error checking best offers for user {user_id}: {e}")

            except Exception as e:
                print(f"[ebay_sync] Error syncing user {user_id}: {e}")

        print("[ebay_sync] Background sync complete")
    except Exception as e:
        print(f"[ebay_sync] Background sync failed: {e}")


@router.post("/backfill-fees")
async def backfill_historical_fees(user: dict = Depends(get_current_user)):
    """
    One-time endpoint to backfill actual eBay fees for historical sales.
    Fetches orders from last 90 days, extracts fees, and updates profit calculations.
    """
    if not user.get("ebay_refresh_token"):
        raise HTTPException(status_code=400, detail="eBay credentials not configured")

    stats = {"updated": 0, "skipped": 0, "errors": 0, "details": []}

    try:
        access_token = await _get_user_ebay_token(user["id"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not get eBay token: {e}")

    # Get user settings
    db_client = get_db()
    user_profile = db_client.table("user_profiles").select(
        "postage_cost"
    ).eq("id", user["id"]).single().execute()

    postage_cost = float((user_profile.data or {}).get("postage_cost") or 1.50)

    # Fetch all orders from last 90 days
    try:
        resp = requests.get(
            "https://api.ebay.com/sell/fulfillment/v1/order",
            headers=_get_ebay_headers(access_token),
            params={
                "sort": "lastModifiedDate:desc",
                "limit": 100,
            },
            timeout=30,
        )

        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail=f"eBay API error: {resp.text}")

        data = resp.json()
        orders = data.get("orders", [])
        print(f"[ebay_sync] Backfill: fetched {len(orders)} orders for user {user['id']}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch eBay orders: {e}")

    # Get all sold items for this user
    inventory = await db.get_all_items(user["id"], status_filter="Sold")
    sold_by_listing = {str(i.get("ebay_listing_id")): i for i in inventory if i.get("ebay_listing_id")}

    # Process each order
    for order in orders:
        try:
            order_id = order.get("orderId")
            line_items = order.get("lineItems", [])

            # Extract fees from order
            # eBay doesn't return ad fees in Fulfillment API, but we can get order-related fees
            order_fees = 0.0
            ebay_charges = order.get("ebayCollectedCharges", {})
            if isinstance(ebay_charges, dict):
                for charge in ebay_charges.get("charges", []):
                    if isinstance(charge, dict):
                        amount_obj = charge.get("amount", {})
                        if isinstance(amount_obj, dict):
                            order_fees += float(amount_obj.get("value", 0))

            for item in line_items:
                sku = item.get("sku", "")
                legacy_id = str(item.get("legacyItemId", ""))

                if not legacy_id or legacy_id not in sold_by_listing:
                    continue

                inv_item = sold_by_listing[legacy_id]

                # Extract sale price
                cost_obj = item.get("lineItemCost", {})
                if isinstance(cost_obj, dict):
                    price_paid = float(cost_obj.get("value", 0))
                else:
                    price_paid = float(cost_obj or 0)

                if not price_paid:
                    stats["skipped"] += 1
                    continue

                # Calculate actual profit with order fees
                # Note: eBay Fulfillment API doesn't return ad fees, only order-related fees
                # We use the order-related fees and estimate ad fees with user's fee rate
                purchase_price = float(inv_item.get("purchase_price") or 0)

                # Order-related fees from ebayCollectedCharges
                item_share_of_fees = order_fees / len(line_items) if line_items else 0

                # Estimate ad fees (promoted listing, insertion fees, etc)
                # eBay doesn't provide these in the API, so we use a conservative estimate
                estimated_ad_fees = round(price_paid * 0.0735, 2)  # ~7.35% for ad/insertion fees

                total_fees = round(item_share_of_fees + estimated_ad_fees, 2)
                net_received = round(price_paid - total_fees, 2)
                profit = round(net_received - purchase_price - postage_cost, 2)

                # Update the inventory item
                current_profit = float(inv_item.get("profit") or 0)

                # Only update if profit changed significantly (more than 1 pence difference)
                if abs(profit - current_profit) > 0.01:
                    await db.edit_item(user["id"], inv_item["item_id"], "profit", profit)
                    stats["updated"] += 1
                    stats["details"].append({
                        "item_id": inv_item["item_id"],
                        "card_name": inv_item.get("card_name"),
                        "old_profit": round(current_profit, 2),
                        "new_profit": profit,
                        "fees": total_fees,
                    })
                    print(f"[ebay_sync] Updated item {inv_item['item_id']}: profit {current_profit} → {profit}")
                else:
                    stats["skipped"] += 1

        except Exception as e:
            stats["errors"] += 1
            print(f"[ebay_sync] Backfill error processing order {order.get('orderId')}: {e}")

    return {
        "success": True,
        "summary": {
            "updated": stats["updated"],
            "skipped": stats["skipped"],
            "errors": stats["errors"],
        },
        "updated_items": stats["details"][:20],  # Return first 20 for brevity
        "note": "Ad fees (promoted listing, insertion) are estimated at 7.35% since eBay Fulfillment API doesn't provide them",
    }
