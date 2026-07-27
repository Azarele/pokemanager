"""
Push notification routes for PokeManager.
Handles Web Push API subscriptions and sending notifications.
"""
import logging
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from web.auth import get_current_user
from web.database import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


class PushSubscription(BaseModel):
    subscription: dict


@router.post("/subscribe")
async def subscribe_to_push(body: PushSubscription, user: dict = Depends(get_current_user)):
    """
    Save a Web Push API subscription for the current user.
    Called when user grants notification permission in the browser.
    """
    try:
        db = get_db()

        # Check if this subscription already exists for this user
        existing = db.table("push_subscriptions").select("id").eq(
            "user_id", user["id"]
        ).eq("subscription", str(body.subscription)).execute()

        if existing.data:
            logger.info(f"[push] User {user['id']} already subscribed")
            return {"success": True, "message": "Already subscribed"}

        # Insert new subscription
        db.table("push_subscriptions").insert({
            "user_id": user["id"],
            "subscription": body.subscription,
        }).execute()

        logger.info(f"[push] User {user['id']} subscribed to push notifications")
        return {"success": True, "message": "Subscribed to push notifications"}

    except Exception as e:
        logger.error(f"[push] Subscription failed for user {user['id']}: {e}")
        return {"success": False, "error": str(e)}


async def send_push_notification(user_id: str, title: str, body: str) -> int:
    """
    Send a push notification to all subscriptions for a given user.
    Returns the number of successful notifications sent.

    Note: This requires pywebpush to be installed and VAPID keys set as env vars.
    """
    try:
        from pywebpush import webpush
        from json import dumps
    except ImportError:
        logger.warning("[push] pywebpush not installed, skipping push notification")
        return 0

    vapid_public = os.getenv("VAPID_PUBLIC_KEY")
    vapid_private = os.getenv("VAPID_PRIVATE_KEY")

    if not vapid_private or not vapid_public:
        logger.warning("[push] VAPID keys not configured, cannot send push notifications")
        return 0

    try:
        db = get_db()

        # Fetch all subscriptions for this user
        result = db.table("push_subscriptions").select("subscription").eq(
            "user_id", user_id
        ).execute()

        subscriptions = result.data or []
        sent_count = 0

        for sub_record in subscriptions:
            try:
                subscription = sub_record.get("subscription")
                if not subscription:
                    continue

                # Send push notification
                webpush(
                    subscription_info=subscription,
                    data=dumps({"title": title, "body": body}),
                    vapid_private_key=vapid_private,
                    vapid_claims={"sub": "mailto:support@pokemanager.com"},
                )
                sent_count += 1

            except Exception as e:
                logger.error(f"[push] Failed to send to subscription: {e}")
                # Continue to next subscription

        logger.info(f"[push] Sent {sent_count} notifications to user {user_id}")
        return sent_count

    except Exception as e:
        logger.error(f"[push] Error sending notifications to user {user_id}: {e}")
        return 0
