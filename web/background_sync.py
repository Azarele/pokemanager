"""
Background tasks for PokeManager.
Handles periodic syncing of eBay live prices and token refresh without blocking user requests.
"""
import asyncio
import logging
from typing import Optional
from datetime import datetime, timezone

import lister_ebay_api
from web.database import get_db
from web import db_inventory as db
from web import user_config

logger = logging.getLogger(__name__)

_sync_task: Optional[asyncio.Task] = None
_token_refresh_task: Optional[asyncio.Task] = None


async def sync_ebay_live_prices():
    """
    Fetch eBay listing prices for all active inventory items across all users.
    Updates live_price in the database and handles bundle price splitting.
    Runs in background without blocking user requests.
    """
    try:
        logger.info("[bg-sync] Starting eBay live price sync")
        db_client = get_db()

        # Fetch all unsold inventory items across all users
        result = db_client.table("inventory_items").select(
            "item_id, user_id, card_name, ebay_listing_id, status"
        ).eq("status", "Inventory").execute()

        all_items = result.data or []
        if not all_items:
            logger.info("[bg-sync] No inventory items to sync")
            return

        logger.info(f"[bg-sync] Fetching prices for {len(all_items)} inventory items")

        # Group items by (user_id, ebay_listing_id) to detect bundles and avoid duplicate API calls
        listings_by_user: dict[str, dict[str, list]] = {}
        for item in all_items:
            user_id = item.get("user_id")
            listing_id = item.get("ebay_listing_id")

            if not user_id or not listing_id:
                continue

            if user_id not in listings_by_user:
                listings_by_user[user_id] = {}
            if listing_id not in listings_by_user[user_id]:
                listings_by_user[user_id][listing_id] = []

            listings_by_user[user_id][listing_id].append(item)

        # Process each user's listings
        total_updated = 0
        total_errors = 0

        for user_id, listings_dict in listings_by_user.items():
            try:
                # Get user profile to access their eBay credentials
                user_result = db_client.table("user_profiles").select(
                    "ebay_app_id, ebay_dev_id, ebay_cert_id, ebay_refresh_token"
                ).eq("id", user_id).single().execute()

                if not user_result.data:
                    logger.warning(f"[bg-sync] User {user_id} not found")
                    continue

                user_data = user_result.data
                # Skip if user doesn't have eBay credentials
                if not user_data.get("ebay_refresh_token"):
                    continue

                # Create a minimal user dict for user_config.apply()
                user_dict = {
                    "id": user_id,
                    "ebay_app_id": user_data.get("ebay_app_id"),
                    "ebay_dev_id": user_data.get("ebay_dev_id"),
                    "ebay_cert_id": user_data.get("ebay_cert_id"),
                    "ebay_refresh_token": user_data.get("ebay_refresh_token"),
                }

                # Fetch prices for this user's listings
                async with user_config.apply(user_dict):
                    for listing_id, items in listings_dict.items():
                        try:
                            # Fetch offer details for this listing
                            offer = await lister_ebay_api.get_offer_details(listing_id)
                            if not offer:
                                logger.debug(f"[bg-sync] No offer found for listing {listing_id}")
                                continue

                            current_price = offer.get("current_price")
                            if not current_price:
                                logger.debug(f"[bg-sync] No price in offer for listing {listing_id}")
                                continue

                            # Detect if this is a true bundle or quantity listing
                            unique_names = set(i.get("card_name", "") for i in items)
                            is_bundle = len(unique_names) > 1

                            # Determine price per item
                            if len(items) > 1 and is_bundle:
                                # True bundle: split price equally
                                price_per_item = round(current_price / len(items), 2)
                            else:
                                # Quantity listing or single item: keep full price
                                price_per_item = current_price

                            # Update live_price for all items sharing this listing
                            for item in items:
                                try:
                                    await db.edit_item(
                                        user_id,
                                        item["item_id"],
                                        "live_price",
                                        price_per_item
                                    )
                                    total_updated += 1
                                except Exception as e:
                                    logger.error(f"[bg-sync] Failed to update item {item['item_id']}: {e}")
                                    total_errors += 1

                            logger.debug(
                                f"[bg-sync] Updated listing {listing_id}: "
                                f"{len(items)} items @ £{price_per_item}"
                            )

                        except Exception as e:
                            logger.error(f"[bg-sync] Error fetching listing {listing_id}: {e}")
                            total_errors += 1

            except Exception as e:
                logger.error(f"[bg-sync] Error processing user {user_id}: {e}")
                total_errors += 1

        logger.info(
            f"[bg-sync] Price sync complete: updated={total_updated}, errors={total_errors}"
        )

    except Exception as e:
        logger.error(f"[bg-sync] Fatal error in price sync: {e}", exc_info=True)


async def background_sync_loop():
    """
    Periodic background sync loop.
    Waits 60 seconds after startup, then syncs every 30 minutes.
    """
    try:
        # Wait 60 seconds before first sync (let app stabilize)
        logger.info("[bg-sync] Scheduling first sync in 60 seconds")
        await asyncio.sleep(60)

        # Run periodic syncs every 30 minutes
        sync_interval = 30 * 60  # 30 minutes in seconds
        while True:
            try:
                await sync_ebay_live_prices()
            except asyncio.CancelledError:
                logger.info("[bg-sync] Background sync cancelled")
                break
            except Exception as e:
                logger.error(f"[bg-sync] Error in sync loop: {e}", exc_info=True)

            # Wait before next sync
            await asyncio.sleep(sync_interval)

    except asyncio.CancelledError:
        logger.info("[bg-sync] Background sync task stopped")
    except Exception as e:
        logger.error(f"[bg-sync] Fatal error in sync loop: {e}", exc_info=True)


async def refresh_ebay_tokens():
    """
    Refresh eBay access tokens for all users who have refresh tokens.
    Updates ebay_access_token and ebay_token_expires_at in database.
    Runs every 12 hours to keep tokens fresh.
    """
    try:
        logger.info("[bg-token] Starting eBay token refresh")
        db_client = get_db()

        # Fetch all users with eBay refresh tokens
        result = db_client.table("user_profiles").select(
            "id, ebay_app_id, ebay_cert_id, ebay_refresh_token"
        ).not_.is_("ebay_refresh_token", "null").execute()

        users_with_tokens = result.data or []
        if not users_with_tokens:
            logger.info("[bg-token] No users with eBay tokens to refresh")
            return

        logger.info(f"[bg-token] Refreshing tokens for {len(users_with_tokens)} users")

        success_count = 0
        error_count = 0

        for user in users_with_tokens:
            user_id = user.get("id")
            try:
                # Create user context with their eBay credentials
                user_dict = {
                    "id": user_id,
                    "ebay_app_id": user.get("ebay_app_id"),
                    "ebay_cert_id": user.get("ebay_cert_id"),
                    "ebay_refresh_token": user.get("ebay_refresh_token"),
                }

                # Refresh the token using eBay API
                async with user_config.apply(user_dict):
                    new_token = await lister_ebay_api.refresh_oauth_token(
                        user.get("ebay_refresh_token")
                    )

                if not new_token:
                    logger.warning(f"[bg-token] Failed to refresh token for user {user_id}")
                    error_count += 1
                    continue

                # Calculate expiration time (eBay tokens usually expire in 24 hours)
                expires_at = datetime.now(timezone.utc).isoformat()

                # Update user profile with new token
                db_client.table("user_profiles").update({
                    "ebay_access_token": new_token.get("access_token"),
                    "ebay_token_expires_at": expires_at,
                }).eq("id", user_id).execute()

                success_count += 1
                logger.info(f"[bg-token] Refreshed token for user {user_id}")

            except Exception as e:
                logger.error(f"[bg-token] Error refreshing token for user {user_id}: {e}")
                error_count += 1

        logger.info(
            f"[bg-token] Token refresh complete: "
            f"success={success_count}, errors={error_count}"
        )

    except Exception as e:
        logger.error(f"[bg-token] Fatal error in token refresh: {e}", exc_info=True)


async def token_refresh_loop():
    """
    Periodic token refresh loop.
    Waits 120 seconds after startup, then refreshes every 12 hours.
    """
    try:
        # Wait 120 seconds before first refresh (let app stabilize)
        logger.info("[bg-token] Scheduling first token refresh in 120 seconds")
        await asyncio.sleep(120)

        # Run periodic refreshes every 12 hours
        refresh_interval = 12 * 60 * 60  # 12 hours in seconds
        while True:
            try:
                await refresh_ebay_tokens()
            except asyncio.CancelledError:
                logger.info("[bg-token] Token refresh cancelled")
                break
            except Exception as e:
                logger.error(f"[bg-token] Error in refresh loop: {e}", exc_info=True)

            # Wait before next refresh
            await asyncio.sleep(refresh_interval)

    except asyncio.CancelledError:
        logger.info("[bg-token] Token refresh task stopped")
    except Exception as e:
        logger.error(f"[bg-token] Fatal error in refresh loop: {e}", exc_info=True)


def start_background_sync() -> asyncio.Task:
    """Start the background sync task. Returns the task handle for cleanup."""
    global _sync_task, _token_refresh_task
    _sync_task = asyncio.create_task(background_sync_loop())
    _token_refresh_task = asyncio.create_task(token_refresh_loop())
    logger.info("[bg-sync] Background sync task started")
    logger.info("[bg-token] Token refresh task started")
    return _sync_task


async def stop_background_sync():
    """Stop the background sync task gracefully."""
    global _sync_task, _token_refresh_task

    if _sync_task and not _sync_task.done():
        logger.info("[bg-sync] Stopping background sync task")
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
        logger.info("[bg-sync] Background sync task stopped")

    if _token_refresh_task and not _token_refresh_task.done():
        logger.info("[bg-token] Stopping token refresh task")
        _token_refresh_task.cancel()
        try:
            await _token_refresh_task
        except asyncio.CancelledError:
            pass
        _token_refresh_task = None
        logger.info("[bg-token] Token refresh task stopped")
