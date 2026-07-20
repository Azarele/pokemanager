"""
Instagram API routes for auto-posting stories with Stripe payment links.
"""
import logging
from datetime import datetime
from io import BytesIO
from fastapi import APIRouter, HTTPException, Request, File, Form, UploadFile
from PIL import Image

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


@router.post("/post-story")
async def post_story(
    item_id: int = Form(...),
    price_override: float = Form(None),
    image: UploadFile = File(None),
    request: Request = None,
):
    """
    Post a Pokémon card to Instagram as a story with a Stripe payment link.

    Accepts multipart/form-data with:
    - item_id: int (required)
    - price_override: float (optional — overrides item price)
    - image: file (optional — uses item image_url if not provided)

    Flow:
    1. Fetch inventory item from Supabase
    2. Create Stripe payment link
    3. Generate story image (using uploaded image or item image URL)
    4. Upload image to Supabase Storage
    5. Post to Instagram with payment link sticker
    6. Update inventory with posting metadata
    """
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    try:
        # Fetch inventory item
        logger.info(f"Fetching item {item_id} for user {user['id']}")
        item = await db.get_item(user["id"], item_id)

        title = item.get("card_name", "Pokémon Card")

        # Use price_override if provided, otherwise use item price
        if price_override and price_override > 0:
            sale_price = price_override
        else:
            sale_price = item.get("sale_price") or item.get("quick_price") or item.get("live_price")

        if not sale_price:
            raise ValueError(f"Item {item_id} has no sale/quick/live price set")

        # Handle image: use uploaded file or fall back to item's image URL
        card_image = None
        if image:
            try:
                image_bytes = await image.read()
                card_image = Image.open(BytesIO(image_bytes)).convert("RGB")
                logger.info(f"Using uploaded image for item {item_id}")
            except Exception as e:
                logger.warning(f"Failed to process uploaded image: {e}")
                card_image = None

        # Fall back to item's image URL if no uploaded image
        if not card_image:
            image_urls = item.get("image_urls", [])
            card_image = image_urls[0] if image_urls else None
            logger.info(f"Using item image URL for item {item_id}: {card_image}")

        logger.info(f"Posting item {item_id} to Instagram: {title} @ £{sale_price}")

        # Step 1: Create Stripe payment link
        logger.info(f"Creating Stripe payment link for {title}")
        payment_link = create_payment_link(title, float(sale_price))

        # Step 2: Generate story image (accepts PIL Image or URL string)
        logger.info("Generating story image")
        image_bytes = generate_story_image(title, float(sale_price), card_image)

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
