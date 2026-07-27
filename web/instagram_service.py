"""
Instagram & Stripe integration for auto-posting stories with payment links.
Handles:
- Stripe payment link creation
- Story image generation with Pillow
- Image upload to Supabase Storage
- Instagram story posting via Meta Graph API
"""
import os
import io
import logging
from datetime import datetime
from typing import Optional

import stripe
import requests
from PIL import Image, ImageDraw, ImageFont

from web.database import get_db

logger = logging.getLogger(__name__)

# Ensure Stripe API key is set
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY
else:
    logger.warning("STRIPE_SECRET_KEY not set — Stripe features disabled")

# Credentials are now fetched per-user from user_profiles table
# These fallback env vars are only for backward compatibility
_FALLBACK_ACCESS_TOKEN = os.getenv("INSTAGRAM_ACCESS_TOKEN")
_FALLBACK_BUSINESS_ACCOUNT_ID = os.getenv("INSTAGRAM_BUSINESS_ACCOUNT_ID")

# Instagram App credentials for token exchange
INSTAGRAM_APP_ID = os.getenv("INSTAGRAM_APP_ID", "1572387654425263")
INSTAGRAM_APP_SECRET = os.getenv("INSTAGRAM_APP_SECRET", "")

SUPABASE_BUCKET = "ig-stories"

if not INSTAGRAM_APP_SECRET:
    logger.warning("INSTAGRAM_APP_SECRET not set — token refresh will not work")


def exchange_for_long_lived_token(short_token: str) -> dict:
    """
    Exchange a short-lived Instagram User Token for a long-lived token.
    Short-lived tokens expire in ~1 hour, long-lived tokens are valid for 60 days.

    Returns: { access_token, expires_in }
    Raises: ValueError if exchange fails
    """
    if not INSTAGRAM_APP_SECRET:
        raise ValueError("INSTAGRAM_APP_SECRET not configured")

    url = "https://graph.instagram.com/access_token"
    params = {
        "grant_type": "ig_exchange_token",
        "client_id": INSTAGRAM_APP_ID,
        "client_secret": INSTAGRAM_APP_SECRET,
        "access_token": short_token,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        data = response.json()

        if "error" in data:
            error_msg = data.get("error", {}).get("message", "Unknown error")
            logger.error(f"Instagram token exchange failed: {data}")
            raise ValueError(f"Token exchange failed: {error_msg}")

        if response.status_code != 200:
            logger.error(f"Instagram token exchange returned {response.status_code}: {data}")
            raise ValueError(f"Token exchange failed with status {response.status_code}")

        access_token = data.get("access_token")
        expires_in = data.get("expires_in")

        if not access_token:
            logger.error(f"No access token in exchange response: {data}")
            raise ValueError("Token exchange returned no access token")

        logger.info(f"Successfully exchanged token, expires in {expires_in} seconds")
        return {"access_token": access_token, "expires_in": expires_in}

    except requests.RequestException as e:
        logger.error(f"Instagram token exchange request failed: {str(e)}")
        raise ValueError(f"Failed to exchange token: {str(e)}")


def get_user_credentials(user_id: str) -> tuple:
    """
    Fetch Instagram credentials for a user from user_profiles using service role key.
    Returns (access_token, business_account_id).
    Raises ValueError if credentials not found or invalid.
    """
    db = get_db()
    user = db.table("user_profiles").select("instagram_access_token, instagram_business_account_id") \
        .eq("id", user_id).execute()

    if not user.data:
        raise ValueError("User not found")

    user_data = user.data[0]
    access_token = user_data.get("instagram_access_token")
    business_account_id = user_data.get("instagram_business_account_id")

    if not access_token or not business_account_id:
        raise ValueError(
            "Instagram credentials not configured. "
            "Visit Settings → Instagram to connect your account."
        )

    return access_token, business_account_id


def create_payment_link(title: str, price_gbp: float) -> str:
    """
    Create a Stripe payment link for the given item.
    Returns the payment link URL.
    """
    if not STRIPE_API_KEY:
        raise ValueError("STRIPE_SECRET_KEY not configured")

    # Create a price on the fly
    price_pence = int(price_gbp * 100)
    stripe_price = stripe.Price.create(
        unit_amount=price_pence,
        currency="gbp",
        product_data={
            "name": title[:100],  # Stripe product names max 100 chars
        },
    )

    # Create payment link
    payment_link = stripe.PaymentLink.create(
        line_items=[
            {"price": stripe_price.id, "quantity": 1},
        ],
    )

    logger.info(f"Created payment link for '{title}': {payment_link.url}")
    return payment_link.url


def generate_story_image(
    title: str, price_gbp: float, card_image = None
) -> bytes:
    """
    Generate a 1080x1920 Instagram story image with:
    - Dark background (#0f0f0f)
    - Card image centered and scaled to ~70% of width
    - White price text bottom-center
    - "AZARAM VAULT" text top-center in muted color
    - Subtle white border around card image

    card_image can be:
    - None: no card image used
    - str: URL to fetch the card image from
    - PIL.Image: already-loaded card image (e.g., from file upload)

    Returns PIL Image as PNG bytes.
    """
    # Story dimensions
    WIDTH, HEIGHT = 1080, 1920

    # Create base image
    img = Image.new("RGB", (WIDTH, HEIGHT), color=(15, 15, 15))
    draw = ImageDraw.Draw(img)

    # Try to load fonts; fall back to default
    try:
        title_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        price_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 64)
        brand_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
    except OSError:
        # Fallback to default font if system fonts unavailable
        title_font = ImageFont.load_default()
        price_font = ImageFont.load_default()
        brand_font = ImageFont.load_default()

    # Draw "AZARAM VAULT" brand text at top
    brand_text = "AZARAM VAULT"
    draw.text(
        (WIDTH // 2, 60),
        brand_text,
        fill=(136, 136, 136),  # #888888
        font=brand_font,
        anchor="mm",
    )

    # Load and place card image if provided
    card_img = None
    if card_image:
        try:
            if isinstance(card_image, str):
                # URL string — fetch and load
                response = requests.get(card_image, timeout=5)
                if response.status_code == 200:
                    card_img = Image.open(io.BytesIO(response.content)).convert("RGB")
                    logger.info(f"Loaded card image from URL: {card_image}")
            else:
                # Assume PIL Image object
                card_img = card_image.convert("RGB") if card_image.mode != "RGB" else card_image
                logger.info("Using provided PIL Image for card")
        except Exception as e:
            logger.warning(f"Failed to process card image: {e}")

    # Place card image if available (scaled to ~70% of width)
    if card_img:
        max_card_width = int(WIDTH * 0.7)
        max_card_height = int(HEIGHT * 0.6)
        card_img.thumbnail((max_card_width, max_card_height), Image.Resampling.LANCZOS)

        # Center card vertically in middle zone
        card_x = (WIDTH - card_img.width) // 2
        card_y = (HEIGHT - card_img.height) // 2 - 100

        # Draw subtle white border around card
        border_width = 3
        border_box = (
            card_x - border_width,
            card_y - border_width,
            card_x + card_img.width + border_width,
            card_y + card_img.height + border_width,
        )
        draw.rectangle(border_box, outline=(255, 255, 255), width=border_width)

        # Paste card onto story
        img.paste(card_img, (card_x, card_y))

    # Draw price at bottom center
    price_text = f"£{price_gbp:.2f}"
    draw.text(
        (WIDTH // 2, HEIGHT - 200),
        price_text,
        fill=(255, 255, 255),
        font=price_font,
        anchor="mm",
    )

    # Draw item title above price
    draw.text(
        (WIDTH // 2, HEIGHT - 100),
        title[:50],  # Truncate long titles
        fill=(255, 255, 255),
        font=title_font,
        anchor="mm",
    )

    # Convert to PNG bytes
    img_bytes = io.BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    return img_bytes.getvalue()


def upload_image_to_supabase(image_bytes: bytes, filename: str) -> str:
    """
    Upload image to Supabase Storage and return public URL.
    File is uploaded to ig-stories bucket using service role key to bypass RLS.
    """
    try:
        db = get_db()
        response = db.storage.from_(SUPABASE_BUCKET).upload(
            filename, image_bytes, {"content-type": "image/png"}
        )
        logger.info(f"Uploaded image to Supabase: {filename}")

        # Get public URL
        public_url = (
            db.storage.from_(SUPABASE_BUCKET)
            .get_public_url(filename)
        )
        return public_url
    except Exception as e:
        logger.error(f"Failed to upload image to Supabase: {e}")
        raise


def post_to_instagram(image_url: str, payment_link: str, user_id: str) -> str:
    """
    Post story to Instagram with payment link sticker.
    Returns the media ID.

    Step 1: Create media container
    Step 2: Publish media
    """
    access_token, business_account_id = get_user_credentials(user_id)
    headers = {"Authorization": f"Bearer {access_token}"}

    # Step 1: Create media container
    logger.info(f"Creating Instagram story container with image: {image_url}")
    container_url = f"https://graph.facebook.com/v19.0/{business_account_id}/media"
    payload = {
        "image_url": image_url,
        "media_type": "STORIES",
        "link_sticker_url": payment_link,
    }

    response = requests.post(container_url, json=payload, headers=headers, timeout=10)
    data = response.json()

    if response.status_code != 200 or "error" in data:
        logger.error(f"Failed to create Instagram media container. Status: {response.status_code}, Full response: {data}")
        error_msg = data.get("error", {}).get("message", response.text) if isinstance(data, dict) else response.text
        raise ValueError(f"Instagram API error: {error_msg}")

    creation_id = data.get("id")
    if not creation_id:
        logger.error(f"No media ID in response: {data}")
        raise ValueError("No media ID returned from Instagram API")

    logger.info(f"Created Instagram media container: {creation_id}")

    # Step 2: Publish media
    logger.info(f"Publishing Instagram story: {creation_id}")
    publish_url = f"https://graph.facebook.com/v19.0/{business_account_id}/media_publish"
    publish_payload = {"creation_id": creation_id}

    response = requests.post(
        publish_url, json=publish_payload, headers=headers, timeout=10
    )
    if response.status_code != 200:
        error_msg = response.text
        logger.error(f"Failed to publish Instagram story: {error_msg}")
        raise ValueError(f"Instagram publish error: {error_msg}")

    data = response.json()
    if "error" in data:
        error_msg = data.get("error", {}).get("message", "Unknown error")
        logger.error(f"Instagram publish error: {error_msg}")
        raise ValueError(f"Instagram publish error: {error_msg}")

    media_id = data.get("id")
    if not media_id:
        logger.error(f"No media ID in publish response: {data}")
        raise ValueError("No media ID returned from publish")

    logger.info(f"Successfully published Instagram story: {media_id}")
    return media_id


async def update_inventory_instagram_metadata(
    user_id: str,
    item_id: int,
    payment_link: str,
    media_id: str,
) -> None:
    """
    Update inventory item with Instagram posting metadata using service role key.
    """
    db = get_db()
    now = datetime.utcnow().isoformat()
    db.table("inventory_items").update({
        "ig_story_posted": True,
        "ig_payment_link": payment_link,
        "ig_media_id": media_id,
        "ig_posted_at": now,
    }).eq("user_id", user_id).eq("item_id", item_id).execute()

    logger.info(f"Updated inventory item {item_id} with Instagram metadata")
