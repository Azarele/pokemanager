"""
ai_helper.py — AI-powered listing content generator using Google Gemini.

Public interface
----------------
    content = await generate_listing_content(
        item_name        = "Charizard Holo Base Set",
        condition        = "graded",
        uk_avg_price_gbp = 45.00,
    )

    content["title"]       → SEO-optimised eBay title (≤ 79 chars)
    content["description"] → 2-paragraph professional TCG description
"""

import re
from typing import Optional

from google import genai

import config

# ---------------------------------------------------------------------------
# Lazy singleton — created only when first needed so a missing key doesn't
# crash the bot on startup if the AI feature is unused.
# ---------------------------------------------------------------------------

_client: Optional[genai.Client] = None

# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are an expert UK eBay and Vinted seller specialising in Pokémon TCG cards.
Generate optimised listing content for the item below.

Item Name: {item_name}
Condition: {condition}
UK Market Price: {price_context}

Requirements:
- TITLE must be strictly under 80 characters (eBay hard limit). Pack it with the \
most searchable keywords: card name, set name (if derivable from the item name), \
a condition hint, and the word "Pokemon" or "TCG".
- DESCRIPTION must be exactly 2 short paragraphs. Paragraph 1: describe the card \
and its condition honestly. Paragraph 2: reassure the buyer about packaging \
(card sleeve, toploader, bubble mailer) and tracked postage.
- Do NOT invent details that were not supplied to you.

Respond in this EXACT format with no other text before or after:
TITLE: [your title here]
DESCRIPTION: [your two-paragraph description here]
"""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_client() -> genai.Client:
    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise ValueError(
                "GEMINI_API_KEY is not set in .env — AI listing assistant is unavailable."
            )
        _client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _client


def _parse_response(raw: str, fallback_name: str) -> dict[str, str]:
    """
    Extract title and description from Gemini's TITLE: / DESCRIPTION: response.

    Uses regex so a stray newline or extra space in the model's output doesn't
    break the parse. Falls back to the original item name / empty description
    if either marker is absent so the listing flow is never blocked.
    """
    title = fallback_name
    description = ""

    title_match = re.search(r"^TITLE:\s*(.+)$", raw, re.MULTILINE | re.IGNORECASE)
    if title_match:
        title = title_match.group(1).strip()[:79]   # hard-cap at 79 chars

    desc_match = re.search(
        r"^DESCRIPTION:\s*([\s\S]+)", raw, re.MULTILINE | re.IGNORECASE
    )
    if desc_match:
        description = desc_match.group(1).strip()

    return {"title": title, "description": description}


# ---------------------------------------------------------------------------
# Public async interface
# ---------------------------------------------------------------------------

async def generate_listing_content(
    item_name: str,
    condition: str,
    uk_avg_price_gbp: Optional[float],
) -> dict[str, str]:
    """
    Call Gemini 1.5 Flash to produce an SEO-optimised title and description.

    Parameters
    ----------
    item_name        : Raw item name from PriceCharting / inventory.
    condition        : Condition string, e.g. "graded", "ungraded", "new".
    uk_avg_price_gbp : UK market average price for context (may be None).

    Returns
    -------
    {"title": str, "description": str}

    Raises
    ------
    ValueError  if GEMINI_API_KEY is missing.
    All other exceptions propagate to bot.py which catches them and falls
    back to the raw item name so the listing flow is never blocked.
    """
    client = _get_client()

    price_context = f"£{uk_avg_price_gbp:.2f}" if uk_avg_price_gbp else "not available"
    prompt = _PROMPT_TEMPLATE.format(
        item_name=item_name,
        condition=condition,
        price_context=price_context,
    )

    response = await client.aio.models.generate_content(
        model='gemini-3.1-flash-lite', 
        contents=prompt
    )
    return _parse_response(response.text, item_name)
