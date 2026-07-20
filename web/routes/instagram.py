"""
Instagram API routes for auto-posting stories with Stripe payment links.
"""
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from web import db_inventory as db
from web.auth import get_current_user
from web.instagram_service import (
    create_payment_link,
    generate_story_image,
    upload_image_to_supabase,
    post_to_instagram,
    update_inventory_instagram_metadata,
)

logger = logging.getLogger(__name__)
router = APIRouter()


class PostStoryRequest(BaseModel):
    item_id: int


@router.post("/post-story")
async def post_story(payload: PostStoryRequest, request: Request):
    """
    Post a Pokémon card to Instagram as a story with a Stripe payment link.

    Flow:
    1. Fetch inventory item from Supabase
    2. Create Stripe payment link
    3. Generate story image using Pillow
    4. Upload image to Supabase Storage
    5. Post to Instagram with payment link sticker
    6. Update inventory with posting metadata
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    item_id = payload.item_id

    try:
        # Fetch inventory item
        logger.info(f"Fetching item {item_id} for user {user['id']}")
        item = await db.get_item(user["id"], item_id)

        title = item.get("card_name", "Pokémon Card")
        sale_price = item.get("sale_price") or item.get("quick_price") or item.get("live_price")

        if not sale_price:
            raise ValueError(f"Item {item_id} has no sale/quick/live price set")

        # Get first image URL if available
        image_urls = item.get("image_urls", [])
        card_image_url = image_urls[0] if image_urls else None

        logger.info(f"Posting item {item_id} to Instagram: {title} @ £{sale_price}")

        # Step 1: Create Stripe payment link
        logger.info(f"Creating Stripe payment link for {title}")
        payment_link = create_payment_link(title, float(sale_price))

        # Step 2: Generate story image
        logger.info("Generating story image")
        image_bytes = generate_story_image(title, float(sale_price), card_image_url)

        # Step 3: Upload to Supabase Storage
        filename = f"ig-story-{user['id']}-{item_id}-{int(datetime.utcnow().timestamp())}.png"
        logger.info(f"Uploading image to Supabase: {filename}")
        image_url = upload_image_to_supabase(image_bytes, filename)

        # Step 4: Post to Instagram
        logger.info("Posting to Instagram")
        media_id = post_to_instagram(image_url, payment_link, user["id"])

        # Step 5: Update inventory
        logger.info(f"Updating inventory item {item_id}")
        await update_inventory_instagram_metadata(user["id"], item_id, payment_link, media_id)

        return {
            "success": True,
            "payment_link": payment_link,
            "ig_media_id": media_id,
            "message": f"Posted '{title}' to Instagram!",
        }

    except ValueError as e:
        logger.error(f"Validation error: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error posting to Instagram: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to post: {str(e)}")
