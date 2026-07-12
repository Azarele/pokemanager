"""
Gemini Vision-powered card identification for Scan & Add and Scan & Sell flows.
"""
import asyncio
import json
import os
import re
import aiohttp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from bs4 import BeautifulSoup

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


async def search_pricecharting(card_name: str, card_number: str = "") -> dict:
    """
    Search PriceCharting for a card by name and optional number.
    Returns {pc_url, pc_name, market_price_gbp} or empty dict if not found.
    """
    try:
        # Build search query: card name + number
        search_q = card_name.strip()
        if card_number:
            search_q = f"{search_q} {card_number}"

        pc_search = (
            "https://www.pricecharting.com/search-products"
            f"?q={search_q.replace(' ', '+')}&type=prices"
        )

        # Fetch search results
        ua = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(pc_search, headers=ua, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return {}
                pc_html = await resp.text()

        # Parse results table
        pc_soup = BeautifulSoup(pc_html, "html.parser")
        rows = pc_soup.select("table#games_table tbody tr")

        best_href = None
        best_name = None

        # Try to find exact match by card number first
        for row in rows[:5]:
            link = row.select_one("td a")
            if not link:
                continue
            href = link.get("href") or ""
            pc_name = link.get_text(strip=True)

            # Check if card number matches URL slug
            if card_number:
                slug_m = re.search(r"-(\d+)$", href.rstrip("/"))
                slug_num = slug_m.group(1).lstrip("0") if slug_m else None
                if slug_num == card_number.lstrip("0"):
                    best_href, best_name = href, pc_name
                    break
                # Also check if card number appears in the name
                if card_number in pc_name:
                    best_href, best_name = href, pc_name
                    break

        # Fallback to first result if no exact match
        if not best_href and rows:
            link = rows[0].select_one("td a")
            if link:
                best_href = link.get("href") or ""
                best_name = link.get_text(strip=True)

        if not best_href:
            return {}

        # Convert relative URL to absolute
        if best_href.startswith("/"):
            pc_url = f"https://www.pricecharting.com{best_href}"
        else:
            pc_url = best_href if best_href.startswith("http") else f"https://www.pricecharting.com/{best_href}"

        # Scrape the price from the product page
        try:
            card_name_from_pc, market_price_usd = await scraper.scrape_card(pc_url, "Near mint or better", "")
            if market_price_usd is None:
                return {"pc_url": pc_url, "pc_name": best_name}

            # Convert USD to GBP
            fx_rate = await asyncio.to_thread(scraper.get_usd_to_gbp)
            market_price_gbp = round(market_price_usd * fx_rate, 2)

            return {
                "pc_url": pc_url,
                "pc_name": best_name,
                "market_price_gbp": market_price_gbp,
            }
        except Exception as e:
            # Return PC URL even if price scrape fails
            print(f"[scan] Failed to scrape price for {pc_url}: {e}")
            return {"pc_url": pc_url, "pc_name": best_name}

    except Exception as e:
        print(f"[scan] PriceCharting search failed for '{card_name}': {e}")
        return {}


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
    model_name = "gemini-3.5-flash"

    try:
        prompt = """Identify this Pokémon card. Return ONLY valid JSON with no markdown, no explanations, no code blocks:
{"card_name": "full card name", "card_number": "number/total", "set_name": "set name", "confidence": "high/medium/low"}
If not a Pokémon card, return: {"error": "not a pokemon card"}"""

        response = client.models.generate_content(
            model=model_name,
            contents=[
                {
                    "parts": [
                        {"inline_data": {"mime_type": req.mime_type, "data": req.image}},
                        {"text": prompt}
                    ]
                }
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

        # If Gemini successfully identified the card, search PriceCharting for pricing
        if "error" not in result and result.get("card_name"):
            pc_data = await search_pricecharting(
                result.get("card_name", ""),
                result.get("card_number", "")
            )
            if pc_data:
                result["pc_url"] = pc_data.get("pc_url")
                result["pc_name"] = pc_data.get("pc_name")
                if "market_price_gbp" in pc_data:
                    result["market_price"] = pc_data["market_price_gbp"]

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
    card_number = req.get("card_number", "").strip()
    if not card_name:
        return {"price": None, "error": "no_card_name"}

    try:
        pc_data = await search_pricecharting(card_name, card_number)
        if not pc_data:
            return {"price": None, "error": "card_not_found_on_pricecharting"}

        return {
            "pc_url": pc_data.get("pc_url"),
            "pc_name": pc_data.get("pc_name"),
            "market_price": pc_data.get("market_price_gbp"),
            "success": True,
        }
    except Exception as e:
        return {"error": str(e)}
