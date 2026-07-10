"""
Gemini Vision-powered card identification for Scan & Add and Scan & Sell flows.
"""
import base64
import json
import os
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

import scraper
import lister_ebay_api
from web import db_inventory as db
from web.auth import get_current_user
from web import user_config

router = APIRouter()


class IdentifyRequest(BaseModel):
    image: str
    mime_type: str = "image/jpeg"


class MatchInventoryRequest(BaseModel):
    card_name: str


@router.post("/identify")
async def identify_card(req: IdentifyRequest, user: dict = Depends(get_current_user)):
    """
    Send image to Gemini Vision to identify Pokémon card.
    Returns JSON with card_name, card_number, set_name, confidence.
    """
    try:
        from google import genai
    except ImportError:
        raise HTTPException(status_code=500, detail="Gemini SDK not installed")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY not configured")

    client = genai.Client(api_key=api_key)

    # Use flash model for faster response (both free and champion use flash for image identification)
    model_name = "gemini-2.0-flash-exp"

    try:
        # Decode base64 image
        image_data = base64.b64decode(req.image)

        prompt = """Identify this Pokémon card. Return ONLY valid JSON with no markdown, no explanations, no code blocks:
{"card_name": "full card name", "card_number": "number/total", "set_name": "set name", "confidence": "high/medium/low"}
If not a Pokémon card, return: {"error": "not a pokemon card"}"""

        # Use the Blob type for image data
        response = await client.aio.models.generate_content(
            model=model_name,
            contents=[
                genai.Content(
                    role="user",
                    parts=[
                        genai.Part.from_text(prompt),
                        genai.Part.from_blob(
                            mime_type=req.mime_type,
                            data=image_data
                        )
                    ]
                )
            ]
        )

        # Parse Gemini response
        text = response.text.strip()
        # Remove markdown code blocks if present
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()

        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = {"error": "could_not_parse_response"}

        return result

    except Exception as e:
        return {"error": f"identification_failed: {str(e)}"}


@router.post("/match-inventory")
async def match_inventory(req: MatchInventoryRequest, user: dict = Depends(get_current_user)):
    """
    Search user's inventory for cards matching the identified card name.
    Returns items with id, card_name, condition, purchase_price, current_price.
    """
    card_name = req.card_name.strip().lower()
    if not card_name:
        return {"matches": []}

    try:
        items = await db.get_all_items(user["id"], status_filter="Inventory")

        # Case-insensitive LIKE match
        matches = [
            {
                "item_id": item["item_id"],
                "card_name": item["card_name"],
                "condition": item["condition"],
                "purchase_price": item["purchase_price"],
                "current_price": item["live_price"] or item["quick_price"],
            }
            for item in items
            if card_name in item["card_name"].lower()
        ]

        return {"matches": matches}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/get-card-price")
async def get_card_price(req: dict, user: dict = Depends(get_current_user)):
    """
    Get current market price for a card from PriceCharting.
    Used to pre-populate price fields in Scan & Add and Scan & Sell flows.
    """
    card_name = req.get("card_name", "").strip()
    if not card_name:
        return {"price": None, "error": "no_card_name"}

    try:
        # Try to search PriceCharting for the card (simplified version)
        # In a real implementation, you'd use the scraper to find the PC URL first
        return {"price": None, "source": "pricecharting_search_not_implemented"}
    except Exception as e:
        return {"error": str(e)}
