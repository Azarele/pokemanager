"""
lister_ebay_api.py — eBay UK listing via official REST/Trading APIs.
 
Public interface (identical to lister_ebay.py — bot.py uses this transparently):
 
    result = await list_item_on_ebay(
        item_name   = "Charizard Holo Base Set",
        price_gbp   = 45.00,
        image_paths = [Path("temp_images/item_5/0.jpg"), ...],
        condition   = "Used",
        description = "Near mint...",
        dry_run     = False,
    )
 
    result.success      → bool
    result.listing_url  → str | None
    result.error        → str | None
 
Setup
-----
Requires EBAY_APP_ID, EBAY_DEV_ID, EBAY_CERT_ID, EBAY_REFRESH_TOKEN in .env.
Run generate_ebay_token.py once to obtain the refresh token.
Business policy IDs (EBAY_FULFILLMENT_POLICY_ID, EBAY_PAYMENT_POLICY_ID,
EBAY_RETURN_POLICY_ID) are also required for publishing.
Run print_policies() to look them up without navigating the eBay UI.
"""
 
import asyncio
import base64
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
 
import requests
 
import config
 
# ---------------------------------------------------------------------------
# Result type (same shape as lister_ebay.py)
# ---------------------------------------------------------------------------
 
@dataclass
class ListingResult:
    platform:    str
    success:     bool
    listing_url: Optional[str] = None
    error:       Optional[str] = None
 
 
# ---------------------------------------------------------------------------
# Module-level token cache — persists across calls within one bot session.
# Only the refresh token is ever persisted to disk (in .env).
# ---------------------------------------------------------------------------
 
_access_token: str   = ""
_token_expiry: float = 0.0  # unix timestamp
 
 
# ---------------------------------------------------------------------------
# Condition maps
# ---------------------------------------------------------------------------
 
# Valid condition IDs for eBay Trading Cards category 183454 (confirmed via metadata API):
#   4000 = "Ungraded" — requires conditionDescriptor 40001 "Card Condition"
#   2750 = "Graded"   — requires conditionDescriptors 27501 "Professional Grader" + 27502 "Grade"
#   3000 = "Used"     — generic fallback with no sub-descriptors; NOT used here
_CONDITION_MAP: dict[str, str] = {
    # Ungraded — all map to conditionId 4000
    "Near mint or better": "4000",
    "Lightly played":      "4000",
    "Moderately played":   "4000",
    "Heavily played":      "4000",
    # Graded — all map to conditionId 2750; grader + grade set via conditionDescriptors
    "PSA 10": "2750", "PSA 9":  "2750", "PSA 8":  "2750", "PSA 7":  "2750",
    "PSA 6":  "2750", "PSA 5":  "2750", "PSA 4":  "2750", "PSA 3":  "2750",
    "PSA 2":  "2750", "PSA 1":  "2750",
    "BGS 10": "2750", "BGS 9.5": "2750", "BGS 9": "2750", "BGS 8.5": "2750", "BGS 8": "2750",
    "CGC 10": "2750", "CGC 9.5": "2750", "CGC 9": "2750", "CGC 8.5": "2750", "CGC 8": "2750",
    "SGC 10": "2750", "SGC 9.5": "2750", "SGC 9": "2750", "SGC 8":  "2750",
    "ACE 10": "2750", "ACE 9.5": "2750", "ACE 9": "2750", "ACE 8":  "2750",
    "GetGraded 10": "2750", "GetGraded 9.5": "2750", "GetGraded 9": "2750", "GetGraded 8": "2750",
}
 
# conditionDescriptor value IDs for conditionId 4000, descriptor 40001 "Card Condition".
# Values confirmed via get_item_condition_policies for category 183454.
_UNGRADED_DESCRIPTOR_MAP: dict[str, str] = {
    "Near mint or better": "400010",
    "Lightly played":      "400015",
    "Moderately played":   "400016",
    "Heavily played":      "400017",
}

# conditionDescriptor value IDs for descriptor 27501 "Professional Grader".
# Values confirmed via get_item_condition_policies (June 2025).
_GRADER_DESCRIPTOR_VALUES: dict[str, str] = {
    "PSA":       "275010",   # Professional Sports Authenticator (PSA)
    "BGS":       "275013",   # Beckett Grading Services (BGS)
    "CGC":       "275015",   # Certified Guaranty Company (CGC)
    "SGC":       "275016",   # Sportscard Guaranty Corporation (SGC)
    "ACE":       "2750119",  # Ace Grading (Ace)
    "GetGraded": "2750123",  # GetGraded — not in eBay's standard list, mapped to Other
}
_GRADER_FALLBACK_VALUE = "2750123"  # Other

# conditionDescriptor value IDs for descriptor 27502 "Grade".
# Keyed by numeric grade string — same IDs apply regardless of grading company.
# Confirmed via get_item_condition_policies for category 183454.
_GRADE_NUMERIC_DESCRIPTOR_MAP: dict[str, str] = {
    "10":  "275020",
    "9.5": "275021",
    "9":   "275022",
    "8.5": "275023",
    "8":   "275024",
    "7.5": "275025",
    "7":   "275026",
    "6.5": "275027",
    "6":   "275028",
    "5.5": "275029",
    "5":   "2750210",
    "4.5": "2750211",
    "4":   "2750212",
    "3.5": "2750213",
    "3":   "2750214",
    "2.5": "2750215",
    "2":   "2750216",
    "1.5": "2750217",
    "1":   "2750218",
}
 
 
def _get_grader(condition_str: str) -> Optional[str]:
    """Extract grader prefix from a condition string like 'PSA 10', 'BGS 9.5', 'GetGraded 10'."""
    condition_str = condition_str or ""
    for grader in ("PSA", "BGS", "CGC", "SGC", "ACE", "GetGraded"):
        if condition_str.upper().startswith(grader.upper()):
            return grader
    return None


def _build_condition_descriptors(condition_str: str) -> list[dict]:
    """Build the conditionDescriptors array for the inventory item PUT body.

    eBay REST Inventory API uses 'name' (descriptor ID as string) and
    'values' (list of value ID strings) — NOT the conditionDescriptorId /
    conditionDescriptorValueId field names from the older Trading API.
    """
    def _descriptor(desc_id: str, value_id: str) -> dict:
        return {
            "name":   desc_id,
            "values": [value_id],
        }

    grader = _get_grader(condition_str)
    if grader:
        grader_value_id = _GRADER_DESCRIPTOR_VALUES.get(grader, _GRADER_FALLBACK_VALUE)
        grade_num = condition_str[len(grader):].strip()
        grade_id = _GRADE_NUMERIC_DESCRIPTOR_MAP.get(grade_num, _GRADE_NUMERIC_DESCRIPTOR_MAP["10"])
        descriptors = [
            _descriptor("27501", grader_value_id),  # Professional Grader
            _descriptor("27502", grade_id),          # Grade
        ]
    else:
        value_id = _UNGRADED_DESCRIPTOR_MAP.get(condition_str)
        if value_id is None:
            value_id = list(_UNGRADED_DESCRIPTOR_MAP.values())[0]
        descriptors = [
            _descriptor("40001", value_id),  # Card Condition
        ]

    return descriptors
 
# Item specifics required by eBay's Trading Cards category (183454).
# All values must be arrays of strings even for single-value fields.
_BASE_ASPECTS: dict[str, list[str]] = {
    "Game":      ["Pokémon"],
    "Card Type": ["Pokémon"],
    "Type":      ["Pokémon"],
}
 
_LANGUAGE_MAP: dict[str, str] = {
    "JP": "Japanese",
    "KR": "Korean",
}
 
# Display strings for the eBay "Condition" item specific (the label buyers see).
_UNGRADED_DISPLAY: dict[str, str] = {
    "Near mint or better": "Ungraded - Near mint or better",
    "Lightly played":      "Ungraded - Lightly played",
    "Moderately played":   "Ungraded - Moderately played",
    "Heavily played":      "Ungraded - Heavily played",
}
_PSA_DISPLAY: dict[str, str] = {
    "PSA 10": "Graded - Gem Mint 10",
    "PSA 9":  "Graded - Mint 9",
    "PSA 8":  "Graded - Near Mint-Mint 8",
    "PSA 7":  "Graded - Near Mint 7",
    "PSA 6":  "Graded - Excellent-Mint 6",
    "PSA 5":  "Graded - Excellent 5",
    "PSA 4":  "Graded - Very Good-Excellent 4",
    "PSA 3":  "Graded - Very Good 3",
    "PSA 2":  "Graded - Good 2",
    "PSA 1":  "Graded - Poor 1",
}
 
 
def _parse_card_specifics(card_name: str, pc_url: str) -> dict[str, list[str]]:
    """Extract card-specific item aspects from the PriceCharting name and URL."""
    aspects: dict[str, list[str]] = {
        "Manufacturer": ["The Pokémon Company"],
        "Game":         ["Pokémon TCG"],
    }
 
    # Card Number: e.g. "271/197" or bare "271" at end-of-string or before "("
    m = re.search(r'\b(\d+(?:/\d+)?)\b(?:\s*\(|$)', card_name)
    if m:
        aspects["Card Number"] = [m.group(1)]
 
    # Set name: "(Pokemon <Set Name>)" in the card name
    m = re.search(r'\(Pokemon\s+(.+?)\)', card_name, re.IGNORECASE)
    if m:
        aspects["Set"] = [m.group(1).strip()]
    elif pc_url:
        # Fallback: derive set from PriceCharting URL path segment
        # e.g. .../game/pokemon-twilight-masquerade/mega-dragonite-ex-271
        parts = pc_url.rstrip("/").split("/")
        if len(parts) >= 2:
            slug = parts[-2]
            if slug.startswith("pokemon-"):
                aspects["Set"] = [slug[len("pokemon-"):].replace("-", " ").title()]
 
    # Character / card name: everything before the first digit group
    m = re.match(r'^(.+?)\s+\d', card_name)
    if m:
        char = re.sub(r'^Pokemon\s+', '', m.group(1), flags=re.IGNORECASE).strip()
        if char:
            aspects["Card Name"] = [char]
 
    return aspects
 
 
def _build_aspects(
    condition_str: str,
    region:        str,
    card_name:     str = "",
    pc_url:        str = "",
) -> dict[str, list[str]]:
    aspects = dict(_BASE_ASPECTS)
    aspects["Language"] = [_LANGUAGE_MAP.get((region or "").upper(), "English")]
 
    grader = _get_grader(condition_str)

    if grader:
        display = _PSA_DISPLAY.get(condition_str, f"Graded — {condition_str}")
        aspects["Condition"]           = [display]
        aspects["Graded"]              = ["Yes"]
        aspects["Professional Grader"] = [grader]
        # Certification Number is omitted — eBay rejects an empty value, and the
        # cert number isn't stored in inventory. Add it manually on eBay if needed.
        grade_num = condition_str[len(grader):].strip()
        if grade_num:
            aspects["Grade"] = [grade_num]
    else:
        display = _UNGRADED_DISPLAY.get(condition_str, f"Ungraded — {condition_str}")
        aspects["Condition"] = [display]
        aspects["Graded"]    = ["No"]
 
    aspects.update(_parse_card_specifics(card_name, pc_url))
    return aspects
 
 
# API endpoints
_TOKEN_URL     = "https://api.ebay.com/identity/v1/oauth2/token"
_TRADING_URL   = "https://api.ebay.com/ws/api.dll"
_INVENTORY_URL = "https://api.ebay.com/sell/inventory/v1"
_ACCOUNT_URL   = "https://api.ebay.com/sell/account/v1"
 
_TOKEN_SCOPES = " ".join([
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
])
 
# MIME type lookup for image uploads
_MIME_TYPES = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".webp": "image/webp",
}
 
 
# ---------------------------------------------------------------------------
# Step 1 — get (or refresh) a short-lived access token
# ---------------------------------------------------------------------------
 
async def _get_access_token() -> str:
    global _access_token, _token_expiry
 
    # Reuse cached token until 5 minutes before expiry
    if _access_token and time.time() < _token_expiry - 300:
        return _access_token
 
    def _exchange() -> dict:
        credentials = base64.b64encode(
            f"{config.EBAY_APP_ID}:{config.EBAY_CERT_ID}".encode()
        ).decode()
        resp = requests.post(
            _TOKEN_URL,
            headers={
                "Authorization": f"Basic {credentials}",
                "Content-Type":  "application/x-www-form-urlencoded",
            },
            data={
                "grant_type":    "refresh_token",
                "refresh_token": config.EBAY_REFRESH_TOKEN,
                "scope":         _TOKEN_SCOPES,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"[ebay_api] Token refresh failed: HTTP {resp.status_code} — {resp.text[:500]}"
            )
        return resp.json()
 
    data = await asyncio.to_thread(_exchange)
    _access_token = data["access_token"]
    _token_expiry = time.time() + data.get("expires_in", 7200)
    print(f"[ebay_api] Access token refreshed (expires in {data.get('expires_in', '?')}s).")
    return _access_token
 
 
# ---------------------------------------------------------------------------
# Step 2 — upload images via Trading API UploadSiteHostedPictures
# ---------------------------------------------------------------------------
 
def _upload_one_image(image_path: Path, access_token: str) -> str:
    """Upload one image to eBay Picture Services. Returns the hosted URL."""
    xml_payload = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<UploadSiteHostedPicturesRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<RequesterCredentials><eBayAuthToken>{access_token}</eBayAuthToken></RequesterCredentials>"
        f"<PictureName>{image_path.name}</PictureName>"
        "</UploadSiteHostedPicturesRequest>"
    )
 
    mime = _MIME_TYPES.get(image_path.suffix.lower(), "image/jpeg")
    with open(image_path, "rb") as f:
        image_bytes = f.read()
 
    resp = requests.post(
        _TRADING_URL,
        headers={
            "X-EBAY-API-CALL-NAME":           "UploadSiteHostedPictures",
            "X-EBAY-API-SITEID":              "3",  # eBay UK
            "X-EBAY-API-APP-NAME":            config.EBAY_APP_ID,
            "X-EBAY-API-DEV-NAME":            config.EBAY_DEV_ID,
            "X-EBAY-API-CERT-NAME":           config.EBAY_CERT_ID,
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
        },
        files=[
            ("XML Payload", ("payload.xml", xml_payload.encode("utf-8"), "text/xml; charset=utf-8")),
            ("image",       (image_path.name, image_bytes, mime)),
        ],
        timeout=60,
    )
 
    if resp.status_code != 200:
        raise RuntimeError(
            f"[ebay_api] UploadSiteHostedPictures failed: HTTP {resp.status_code} — {resp.text[:500]}"
        )
 
    try:
        root = ET.fromstring(resp.text)
        ns   = {"e": "urn:ebay:apis:eBLBaseComponents"}
        url  = root.find(".//e:FullURL", ns)
        if url is None or not url.text:
            raise ValueError("FullURL element not found")
        return url.text
    except Exception as exc:
        raise RuntimeError(
            f"[ebay_api] Could not parse image upload response: {exc}\nBody: {resp.text[:500]}"
        )
 
 
async def _upload_images(image_paths: list[Path], access_token: str) -> list[str]:
    """Upload up to 12 images (eBay UK limit) and return their hosted URLs."""
    paths = image_paths[:12]
    urls: list[str] = []
    for i, path in enumerate(paths):
        url = await asyncio.to_thread(_upload_one_image, path, access_token)
        print(f"[ebay_api] Uploaded image {i + 1}/{len(paths)} → {url}")
        urls.append(url)
    return urls
 
 
# ---------------------------------------------------------------------------
# Step 3 — map condition string to eBay condition ID
# ---------------------------------------------------------------------------
 
def _map_condition(condition_str: str) -> str:
    return _CONDITION_MAP.get(condition_str, "4000")
 
 
# ---------------------------------------------------------------------------
# Step 4 — create inventory item, offer, and publish via Inventory API
# ---------------------------------------------------------------------------
 
def _json_headers(access_token: str) -> dict:
    return {
        "Authorization":    f"Bearer {access_token}",
        "Content-Type":     "application/json",
        "Content-Language": "en-GB",
    }
 
 
def _create_inventory_item(
    sku:           str,
    title:         str,
    description:   str,
    condition_str: str,
    image_urls:    list[str],
    access_token:  str,
    aspects:       dict,
) -> None:
    """PUT /sell/inventory/v1/inventory_item/{sku} — 204 on success."""
    grader = _get_grader(condition_str)
    if grader:
        cond_desc = f"{condition_str} — professionally graded and slabbed."
    else:
        cond_desc = condition_str or description

    body = {
        "availability": {
            "shipToLocationAvailability": {"quantity": 1}
        },
        "condition":             "LIKE_NEW" if grader else "USED_VERY_GOOD",
        "conditionDescription":  cond_desc,
        "conditionDescriptors":  _build_condition_descriptors(condition_str),
        "product": {
            "title":       title[:80],
            "description": description,
            "imageUrls":   image_urls,
            "aspects":     aspects,
        },
    }
 
    resp = requests.put(
        f"{_INVENTORY_URL}/inventory_item/{sku}",
        headers=_json_headers(access_token),
        json=body,
        timeout=30,
    )
 
    if resp.status_code not in (200, 204):
        raise RuntimeError(
            f"[ebay_api] Create inventory item failed: HTTP {resp.status_code} — {resp.text[:500]}"
        )
 
 
def _create_offer(
    sku:          str,
    price_gbp:    float,
    description:  str,
    condition_id: str,
    access_token: str,
    category_id:  Optional[str] = None,
) -> str:
    """POST /sell/inventory/v1/offer — returns offer ID."""
    if category_id is None:
        category_id = config.EBAY_CATEGORY_ID
 
    body = {
        "sku":                sku,
        "marketplaceId":      "EBAY_GB",
        "format":             "FIXED_PRICE",
        "availableQuantity":  1,
        "categoryId":         category_id,
        "conditionId":        condition_id,
        "listingDescription": description,
        "pricingSummary": {
            "price": {
                "value":    f"{price_gbp:.2f}",
                "currency": "GBP",
            }
        },
        "merchantLocationKey": "default",
        "listingPolicies": {
            "fulfillmentPolicyId": config.EBAY_FULFILLMENT_POLICY_ID,
            "paymentPolicyId":     config.EBAY_PAYMENT_POLICY_ID,
            "returnPolicyId":      config.EBAY_RETURN_POLICY_ID,
        },
    }
 
    resp = requests.post(
        f"{_INVENTORY_URL}/offer",
        headers=_json_headers(access_token),
        json=body,
        timeout=30,
    )
 
    if resp.status_code not in (200, 201):
        body_text = resp.text[:500]
        print(f"[ebay_api] Create offer failed: HTTP {resp.status_code} — {body_text}")
 
        # errorId 25002 — offer already exists; extract its ID, update price, and reuse it
        if resp.status_code == 400:
            try:
                errors = resp.json().get("errors", [])
            except Exception:
                errors = []
            for err in errors:
                if err.get("errorId") == 25002:
                    for param in err.get("parameters", []):
                        if param.get("name") == "offerId":
                            existing_offer_id = param["value"]
                            print(f"[ebay_api] Offer already exists ({existing_offer_id}) — updating price and reusing.")
                            _patch_offer_price(existing_offer_id, price_gbp, access_token)
                            return existing_offer_id
 
        # Retry once with the fallback category if the configured one was rejected
        if category_id != "183050" and "category" in body_text.lower():
            print(f"[ebay_api] Category {category_id} rejected — retrying with 183050 (Trading Card Games).")
            return _create_offer(sku, price_gbp, description, condition_id, access_token, "183050")
 
        raise RuntimeError(
            f"[ebay_api] Create offer failed: HTTP {resp.status_code} — {body_text}"
        )
 
    data     = resp.json()
    offer_id = data.get("offerId")
    if not offer_id:
        raise RuntimeError(f"[ebay_api] No offerId in create-offer response: {data}")
    return offer_id
 
 
def _publish_offer(offer_id: str, access_token: str) -> str:
    """POST /sell/inventory/v1/offer/{offerId}/publish — returns listing URL.
 
    eBay returns HTTP 400 with error 25058 for trading card condition warnings
    but still publishes the listing and includes a listingId in the response.
    If listingId is present, the listing is live regardless of HTTP status.
    """
    resp = requests.post(
        f"{_INVENTORY_URL}/offer/{offer_id}/publish",
        headers=_json_headers(access_token),
        timeout=30,
    )
 
    try:
        data = resp.json()
    except Exception:
        data = {}
 
    listing_id = data.get("listingId")
    if listing_id:
        # Listing is live — log any warnings but don't fail
        for err in data.get("errors", []):
            print(f"[ebay_api] Publish note: {err.get('message', '')[:120]}")
        return f"https://www.ebay.co.uk/itm/{listing_id}"
 
    # No listingId — genuine failure
    raise RuntimeError(
        f"[ebay_api] Publish offer failed: HTTP {resp.status_code} — {resp.text[:500]}"
    )
 
 
async def _create_listing(
    title:         str,
    description:   str,
    price_gbp:     float,
    condition_id:  str,
    condition_str: str,
    image_urls:    list[str],
    access_token:  str,
    dry_run:       bool,
    aspects:       dict,
    item_id:       int = 0,
    sku:           str = "",
) -> str:
    sku = sku or (f"pokemaz-{item_id}" if item_id else f"pokemaz-{int(time.time())}")
 
    # 4a — create inventory item (always, even for dry_run, to validate the payload)
    await asyncio.to_thread(
        _create_inventory_item,
        sku, title, description, condition_str, image_urls, access_token, aspects,
    )
    print(f"[ebay_api] Inventory item created (SKU: {sku}).")
 
    if dry_run:
        print("[ebay_api] dry_run=True — stopping before offer creation.")
        return "DRY_RUN — not published"
 
    # 4b — create offer
    offer_id = await asyncio.to_thread(
        _create_offer,
        sku, price_gbp, description, condition_id, access_token,
    )
    print(f"[ebay_api] Offer created (ID: {offer_id}).")
 
    # 4c — publish
    listing_url = await asyncio.to_thread(_publish_offer, offer_id, access_token)
    print(f"[ebay_api] Listing published → {listing_url}")
    return listing_url
 
 
# ---------------------------------------------------------------------------
# Application-token cache — Client Credentials grant, needed for Buy APIs
# ---------------------------------------------------------------------------
 
_app_access_token: str   = ""
_app_token_expiry: float = 0.0

# Competitor price cache — keyed by "card_name|condition", TTL 1 hour
_competitor_cache: dict[str, tuple[dict, float]] = {}
_COMPETITOR_CACHE_TTL = 3600  # seconds


def _refresh_app_token() -> str:
    global _app_access_token, _app_token_expiry
    if _app_access_token and time.time() < _app_token_expiry - 60:
        return _app_access_token
    credentials = base64.b64encode(
        f"{config.EBAY_APP_ID}:{config.EBAY_CERT_ID}".encode()
    ).decode()
    resp = requests.post(
        _TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type":  "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "client_credentials",
            "scope":      "https://api.ebay.com/oauth/api_scope",
        },
        timeout=15,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"App token failed: HTTP {resp.status_code}")
    data = resp.json()
    _app_access_token = data["access_token"]
    _app_token_expiry = time.time() + data.get("expires_in", 7200)
    return _app_access_token
 
 
async def get_competitor_prices(title: str) -> list[float]:
    """
    Return up to 3 lowest active BIN prices for similar cards on eBay UK.
    Uses first 5 words of title. Never raises — returns [] on any failure.
    """
    def _fetch() -> list[float]:
        try:
            token = _refresh_app_token()
        except Exception as exc:
            print(f"[ebay_api] App token failed: {exc}")
            return []
        query = " ".join(title.split()[:5])
        try:
            resp = requests.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                headers={
                    "Authorization":           f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
                },
                params={
                    "q":            query,
                    "filter":       "buyingOptions:{FIXED_PRICE}",
                    "limit":        "10",
                    "category_ids": config.EBAY_CATEGORY_ID,
                },
                timeout=10,
            )
        except Exception as exc:
            print(f"[ebay_api] Competitor search failed: {exc}")
            return []
        if resp.status_code != 200:
            print(f"[ebay_api] Browse API HTTP {resp.status_code}")
            return []
        prices: list[float] = []
        for item in resp.json().get("itemSummaries", []):
            try:
                prices.append(float(item["price"]["value"]))
            except (KeyError, ValueError):
                pass
        return sorted(prices)[:3]

    return await asyncio.to_thread(_fetch)


def _filter_outliers(prices: list[float]) -> list[float]:
    """Remove prices wildly inconsistent with the group (likely mismatched listings)."""
    if len(prices) < 4:
        return prices
    sorted_p = sorted(prices)
    median = sorted_p[len(sorted_p) // 2]
    filtered = [p for p in prices if median * 0.2 <= p <= median * 5.0]
    return filtered if len(filtered) >= 2 else prices


async def get_competitor_price(card_name: str, condition: str = "") -> dict | None:
    """
    Search eBay UK active BIN listings for this card and return pricing data.
    Results are cached per (card_name, condition) for _COMPETITOR_CACHE_TTL seconds.
    Never raises — returns None on any failure.

    Pricing strategy scales with market depth:
    - 0-1 competitors: price near market value (light 2% undercut, don't race to bottom)
    - 2-4 competitors: undercut the lowest by 3%
    - 5-9 competitors: undercut the 2nd-lowest by 4%
    - 10+ competitors: undercut the 3rd-lowest by 5% (saturated market)

    Returns:
    {
        "lowest":        float,      # cheapest active listing (after outlier filter)
        "median":        float,      # median of active listings
        "count":         int,        # number of listings found (raw, before filter)
        "quick_sell":    float,      # competitor-aware quick-sell price
        "strategy_note": str,        # human-readable explanation of the pricing logic
        "target_price":  float,      # the specific competitor price used as reference
        "urls":          list[str],  # first 3 listing URLs for reference
    }
    """
    cache_key = f"{card_name.lower()}|{condition.lower()}"
    cached = _competitor_cache.get(cache_key)
    if cached is not None:
        data, ts = cached
        if time.time() - ts < _COMPETITOR_CACHE_TTL:
            return data

    def _fetch() -> dict | None:
        try:
            token = _refresh_app_token()
        except Exception as exc:
            print(f"[ebay_api] App token failed: {exc}")
            return None

        words = card_name.split()[:5]
        grader = _get_grader(condition)
        query = " ".join(words) + (f" {condition}" if grader else "")

        try:
            resp = requests.get(
                "https://api.ebay.com/buy/browse/v1/item_summary/search",
                headers={
                    "Authorization":           f"Bearer {token}",
                    "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
                },
                params={
                    "q":            query,
                    "filter":       "buyingOptions:{FIXED_PRICE},itemLocationCountry:GB",
                    "limit":        "20",
                    "category_ids": config.EBAY_CATEGORY_ID,
                },
                timeout=10,
            )
        except Exception as exc:
            print(f"[ebay_api] Competitor search failed: {exc}")
            return None

        if resp.status_code != 200:
            print(f"[ebay_api] Browse API HTTP {resp.status_code}")
            return None

        raw_prices: list[float] = []
        urls: list[str] = []
        for summary in resp.json().get("itemSummaries", []):
            try:
                raw_prices.append(float(summary["price"]["value"]))
                if len(urls) < 3:
                    web_url = summary.get("itemWebUrl", "")
                    if web_url:
                        urls.append(web_url)
            except (KeyError, ValueError):
                pass

        if not raw_prices:
            return None

        raw_count = len(raw_prices)
        prices = _filter_outliers(raw_prices)
        prices_sorted = sorted(prices)
        count = len(prices_sorted)

        if count <= 1:
            target_price = prices_sorted[0] if prices_sorted else None
            quick_sell = round(target_price * 0.98, 2) if target_price else None
            strategy_note = "minimal competition — light undercut"
        elif count <= 4:
            target_price = prices_sorted[0]
            quick_sell = round(target_price * 0.97, 2)
            strategy_note = f"undercut lowest of {raw_count} by 3%"
        elif count <= 9:
            target_price = prices_sorted[1]
            quick_sell = round(target_price * 0.96, 2)
            strategy_note = f"undercut 2nd-lowest of {raw_count} by 4%"
        else:
            target_price = prices_sorted[2]
            quick_sell = round(target_price * 0.95, 2)
            strategy_note = f"undercut 3rd-lowest of {raw_count} by 5% (saturated market)"

        if quick_sell is None or target_price is None:
            return None

        return {
            "lowest":        prices_sorted[0],
            "median":        prices_sorted[count // 2],
            "count":         raw_count,
            "quick_sell":    quick_sell,
            "strategy_note": strategy_note,
            "target_price":  target_price,
            "urls":          urls,
        }

    result = await asyncio.to_thread(_fetch)
    if result is not None:
        _competitor_cache[cache_key] = (result, time.time())
    return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
 
async def list_item_on_ebay(
    item_name:   str,
    price_gbp:   float,
    image_paths: list[Path],
    condition:   str = "Used",
    description: str = "",
    dry_run:     bool = False,
    region:      str = "",
    card_name:   str = "",
    pc_url:      str = "",
    item_id:     int = 0,
    sku:         str = "",
) -> ListingResult:
    """
    Create a new eBay UK listing and return a ListingResult.
 
    Parameters
    ----------
    item_name   : Listing title (truncated to 80 chars).
    price_gbp   : Buy-it-now price in GBP.
    image_paths : Local image files to upload (max 12 used).
    condition   : Condition string matching _CONDITION_MAP keys.
    description : Item description text.
    dry_run     : If True, create inventory item but skip offer and publish.
    region      : Card region ("JP", "KR", or "" for English). Sets the Language aspect.
    card_name   : Raw card name from inventory (used to extract card specifics).
    pc_url      : PriceCharting URL (used to derive set name if not in card_name).
    """
    try:
        # eBay enforces a minimum listing price — clamp anything below it.
        original_price = price_gbp
        price_gbp = max(price_gbp, config.EBAY_MIN_PRICE_GBP)
        if price_gbp != original_price:
            print(f"[ebay_api] Price raised to minimum £{config.EBAY_MIN_PRICE_GBP} (was £{original_price:.2f})")

        access_token = await _get_access_token()
        image_urls   = await _upload_images(image_paths, access_token)
        condition_id = _map_condition(condition)
        aspects      = _build_aspects(condition, region, card_name, pc_url)
        listing_url  = await _create_listing(
            title          = item_name,
            description    = description,
            price_gbp      = price_gbp,
            condition_id   = condition_id,
            condition_str  = condition,
            image_urls     = image_urls,
            access_token   = access_token,
            dry_run        = dry_run,
            aspects        = aspects,
            item_id        = item_id,
            sku            = sku,
        )
        return ListingResult(platform="eBay", success=True, listing_url=listing_url)
 
    except Exception as exc:
        print(f"[ebay_api] Listing failed: {exc}")
        return ListingResult(platform="eBay", success=False, error=str(exc))
 
 
# ---------------------------------------------------------------------------
# End listing — Trading API EndItem
# ---------------------------------------------------------------------------
 
def _end_item(listing_id: str, access_token: str) -> None:
    """Send EndItem to the Trading API. Raises on failure or eBay-side rejection."""
    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<EndItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<RequesterCredentials><eBayAuthToken>{access_token}</eBayAuthToken></RequesterCredentials>"
        f"<ItemID>{listing_id}</ItemID>"
        "<EndingReason>NotAvailable</EndingReason>"
        "</EndItemRequest>"
    )
 
    resp = requests.post(
        _TRADING_URL,
        headers={
            "X-EBAY-API-CALL-NAME":           "EndItem",
            "X-EBAY-API-SITEID":              "3",  # eBay UK
            "X-EBAY-API-APP-NAME":            config.EBAY_APP_ID,
            "X-EBAY-API-DEV-NAME":            config.EBAY_DEV_ID,
            "X-EBAY-API-CERT-NAME":           config.EBAY_CERT_ID,
            "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
            "Content-Type":                   "text/xml; charset=utf-8",
        },
        data=xml_body.encode("utf-8"),
        timeout=30,
    )
 
    if resp.status_code != 200:
        raise RuntimeError(
            f"[ebay_api] EndItem failed: HTTP {resp.status_code} — {resp.text[:500]}"
        )
 
    try:
        root = ET.fromstring(resp.text)
        ns   = {"e": "urn:ebay:apis:eBLBaseComponents"}
        ack  = root.find("e:Ack", ns)
        if ack is None or ack.text not in ("Success", "Warning"):
            long_msg = root.find(".//e:LongMessage", ns)
            msg = (long_msg.text if long_msg is not None and long_msg.text else resp.text[:300])
            raise RuntimeError(f"[ebay_api] EndItem rejected: {msg}")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"[ebay_api] Could not parse EndItem response: {exc}\nBody: {resp.text[:500]}"
        )
 
 
async def end_ebay_listing(listing_id: str) -> bool:
    """
    End an active eBay listing by item ID (the numeric ID from /itm/XXXX).
    Returns True on success, raises on failure.
    """
    access_token = await _get_access_token()
    await asyncio.to_thread(_end_item, listing_id, access_token)
    print(f"[ebay_api] Listing {listing_id} ended.")
    return True


def _check_listing_active_sync(listing_id: str) -> bool:
    try:
        token = _refresh_app_token()
    except Exception as exc:
        print(f"[ebay_verify] Token error for {listing_id}: {exc}")
        return True  # assume active on auth failure

    try:
        resp = requests.get(
            f"https://api.ebay.com/buy/browse/v1/item/v1|{listing_id}|0",
            headers={
                "Authorization":           f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_GB",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return "FIXED_PRICE" in data.get("buyingOptions", [])
        elif resp.status_code == 404:
            return False  # ended or not found
        else:
            # 403, 429, 5xx — don't risk a false removal
            print(f"[ebay_verify] HTTP {resp.status_code} for listing {listing_id} — skipping")
            return True
    except Exception as exc:
        print(f"[ebay_verify] Error checking {listing_id}: {exc}")
        return True


async def check_listing_active(listing_id: str) -> bool:
    """
    Check if an eBay listing is still active using the Browse API.
    Returns True if active, False if ended/sold/not found.
    Conservatively returns True on any network or auth error to avoid false removals.
    """
    return await asyncio.to_thread(_check_listing_active_sync, listing_id)
 
 
# ---------------------------------------------------------------------------
# eBay price sync — update an existing offer's BIN price
# ---------------------------------------------------------------------------

async def get_offer_details(listing_id: str) -> dict | None:
    """
    Return {offer_id, current_price} for an active eBay listing, or None on failure.
    Uses the Sell Inventory API with the user OAuth token.
    """
    try:
        access_token = await _get_access_token()
    except Exception as exc:
        print(f"[ebay_api] get_offer_details token failed: {exc}")
        return None

    def _fetch() -> dict | None:
        resp = requests.get(
            f"{_INVENTORY_URL}/offer",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Language": "en-GB",
            },
            params={"listing_id": listing_id},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[ebay_api] get_offer_details HTTP {resp.status_code} for listing {listing_id}")
            return None
        offers = resp.json().get("offers", [])
        if not offers:
            return None
        offer = offers[0]
        try:
            current_price = float(
                offer.get("pricingSummary", {}).get("price", {}).get("value", 0)
            )
        except (TypeError, ValueError):
            current_price = 0.0
        return {
            "offer_id":      offer.get("offerId"),
            "current_price": current_price,
        }

    return await asyncio.to_thread(_fetch)


async def update_offer_price_direct(offer_id: str, new_price_gbp: float) -> bool:
    """
    Update price by offer_id directly — skips the GET used by update_offer_price.
    Returns True on success. Never raises.
    """
    new_price_gbp = max(round(new_price_gbp, 2), config.EBAY_MIN_PRICE_GBP)
    try:
        access_token = await _get_access_token()
    except Exception as exc:
        print(f"[price_sync] Token refresh failed: {exc}")
        return False

    def _put() -> bool:
        resp = requests.put(
            f"{_INVENTORY_URL}/offer/{offer_id}",
            headers=_json_headers(access_token),
            json={
                "pricingSummary": {
                    "price": {"value": f"{new_price_gbp:.2f}", "currency": "GBP"}
                }
            },
            timeout=15,
        )
        if resp.status_code not in (200, 204):
            print(f"[price_sync] PUT offer/{offer_id}: HTTP {resp.status_code} — {resp.text[:200]}")
            return False
        return True

    return await asyncio.to_thread(_put)


async def update_offer_price(listing_id: str, new_price_gbp: float) -> bool:
    """
    Update the BIN price of an existing eBay offer. Returns True on success.
    Never raises — logs warnings and returns False on any failure.
    Applies EBAY_MIN_PRICE_GBP floor.
    """
    new_price_gbp = max(round(new_price_gbp, 2), config.EBAY_MIN_PRICE_GBP)

    try:
        access_token = await _get_access_token()
    except Exception as exc:
        print(f"[price_sync] Token refresh failed: {exc}")
        return False

    def _do() -> bool:
        # Locate offer by listing ID
        resp = requests.get(
            f"{_INVENTORY_URL}/offer",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Language": "en-GB",
            },
            params={"listing_id": listing_id},
            timeout=15,
        )
        if resp.status_code != 200:
            print(f"[price_sync] GET offer for listing {listing_id}: HTTP {resp.status_code}")
            return False
        offers = resp.json().get("offers", [])
        if not offers:
            print(f"[price_sync] No offer found for listing {listing_id}")
            return False
        offer_id = offers[0].get("offerId")
        if not offer_id:
            return False

        # Update price
        resp2 = requests.put(
            f"{_INVENTORY_URL}/offer/{offer_id}",
            headers=_json_headers(access_token),
            json={
                "pricingSummary": {
                    "price": {"value": f"{new_price_gbp:.2f}", "currency": "GBP"}
                }
            },
            timeout=15,
        )
        if resp2.status_code not in (200, 204):
            print(f"[price_sync] PUT offer/{offer_id}: HTTP {resp2.status_code} — {resp2.text[:200]}")
            return False
        return True

    return await asyncio.to_thread(_do)


# ---------------------------------------------------------------------------
# Trading API — revise any active listing price (works regardless of origin)
# ---------------------------------------------------------------------------

def _get_offer_id_by_sku(sku: str, access_token: str) -> Optional[str]:
    """Look up an Inventory API offer ID by SKU. Returns None if not found."""
    resp = requests.get(
        f"{_INVENTORY_URL}/offer",
        headers={"Authorization": f"Bearer {access_token}", "Content-Language": "en-GB"},
        params={"sku": sku, "marketplace_id": "EBAY_GB"},
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"[ebay_api] GET offer?sku={sku}: HTTP {resp.status_code} — {resp.text[:200]}")
        return None
    offers = resp.json().get("offers", [])
    return offers[0].get("offerId") if offers else None


def _read_offer_price(offer_id: str, access_token: str) -> Optional[float]:
    """Return the current price of an offer, or None if it can't be read."""
    resp = requests.get(
        f"{_INVENTORY_URL}/offer/{offer_id}",
        headers={"Authorization": f"Bearer {access_token}", "Content-Language": "en-GB"},
        timeout=15,
    )
    if resp.status_code != 200:
        return None
    try:
        return float(resp.json().get("pricingSummary", {}).get("price", {}).get("value"))
    except (TypeError, ValueError):
        return None


def _patch_offer_price(offer_id: str, new_price_gbp: float, access_token: str) -> bool:
    """
    Set the offer's price via the Inventory API and confirm it stuck.

    eBay's updateOffer (PUT /offer/{offerId}) is a full replace with no PATCH
    variant. Sending a price-only body makes eBay reject it with a spurious
    HTTP 400 / errorId 25016 ("description is required") — yet it still applies
    the new price. Resubmitting the whole offer to avoid the 400 would mean
    overwriting listingDescription (which the GET doesn't even return), risking
    clobbering each listing's real description. So we send the price-only body
    and trust a read-back, not the status code: success means the live offer
    price now equals the target.
    """
    resp = requests.put(
        f"{_INVENTORY_URL}/offer/{offer_id}",
        headers=_json_headers(access_token),
        json={"pricingSummary": {"price": {"value": f"{new_price_gbp:.2f}", "currency": "GBP"}}},
        timeout=15,
    )
    if resp.status_code in (200, 204):
        return True

    # Non-2xx — eBay may still have applied the price. Verify by reading it back.
    applied = _read_offer_price(offer_id, access_token)
    if applied is not None and abs(applied - new_price_gbp) < 0.005:
        return True

    print(f"[ebay_api] PUT offer/{offer_id}: HTTP {resp.status_code} — {resp.text[:200]}")
    return False


def _revise_via_trading_api(listing_id: str, new_price_gbp: float, access_token: str) -> bool:
    """
    Revise price via the Trading API ReviseFixedPriceItem call. Unlike the
    Inventory API, this works on listings created through Sell-Your-Item, the
    mobile app, or any other channel — not just Inventory API listings.
    """
    xml_body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<ReviseFixedPriceItemRequest xmlns="urn:ebay:apis:eBLBaseComponents">'
        f"<RequesterCredentials><eBayAuthToken>{access_token}</eBayAuthToken></RequesterCredentials>"
        "<Item>"
        f"<ItemID>{listing_id}</ItemID>"
        f'<StartPrice currencyID="GBP">{new_price_gbp:.2f}</StartPrice>'
        "</Item>"
        "</ReviseFixedPriceItemRequest>"
    )
    try:
        resp = requests.post(
            _TRADING_URL,
            headers={
                "X-EBAY-API-COMPATIBILITY-LEVEL": "967",
                "X-EBAY-API-CALL-NAME":           "ReviseFixedPriceItem",
                "X-EBAY-API-SITEID":              "3",
                "Content-Type":                   "text/xml; charset=utf-8",
                "X-EBAY-API-APP-NAME":            config.EBAY_APP_ID,
                "X-EBAY-API-DEV-NAME":            config.EBAY_DEV_ID,
                "X-EBAY-API-CERT-NAME":           config.EBAY_CERT_ID,
            },
            data=xml_body.encode("utf-8"),
            timeout=15,
        )
        if not resp.text.strip():
            print(f"[ebay_api] ReviseFixedPriceItem: empty response for {listing_id} (HTTP {resp.status_code})")
            return False
        root = ET.fromstring(resp.text)
        ns   = {"eb": "urn:ebay:apis:eBLBaseComponents"}
        ack  = root.find("eb:Ack", ns)
        if ack is not None and ack.text in ("Success", "Warning"):
            return True
        for err in root.findall(".//eb:ShortMessage", ns):
            print(f"[ebay_api] ReviseFixedPriceItem error for {listing_id}: {err.text}")
        return False
    except Exception as exc:
        print(f"[ebay_api] ReviseFixedPriceItem exception for {listing_id}: {exc}")
        return False


async def revise_listing_price(listing_id: str, new_price_gbp: float, sku: str = "") -> bool:
    """
    Update the price of an eBay listing.

    Bot-created listings live in the Inventory API and can only be revised
    there — Trading API's ReviseFixedPriceItem doesn't see them. So this
    first looks up the offer by SKU (the Inventory API's getOffers call only
    supports filtering by SKU, not listing/item ID) and PATCHes the price
    there. If no offer is found for that SKU, the listing was created
    outside the Inventory API (Sell-Your-Item, mobile app, etc, or simply
    predates the bot's SKU scheme) and we fall back to ReviseFixedPriceItem.
    Returns True on success, False on any failure (never raises).
    """
    new_price_gbp = max(round(new_price_gbp, 2), config.EBAY_MIN_PRICE_GBP)

    try:
        access_token = await _get_access_token()
    except Exception as exc:
        print(f"[ebay_api] revise_listing_price: token refresh failed: {exc}")
        return False

    def _do() -> bool:
        offer_id = _get_offer_id_by_sku(sku, access_token) if sku else None
        if offer_id and _patch_offer_price(offer_id, new_price_gbp, access_token):
            return True
        return _revise_via_trading_api(listing_id, new_price_gbp, access_token)

    return await asyncio.to_thread(_do)


async def test_revise_price(listing_id: str, price: float, sku: str = "") -> None:
    """Debug helper — test price revision on a single listing.

    The Inventory-API helpers are synchronous (they use blocking ``requests``),
    so they're dispatched with ``asyncio.to_thread`` here exactly as
    ``revise_listing_price`` does internally — not awaited directly.

    Run as:
        python -c "import asyncio, lister_ebay_api; asyncio.run(lister_ebay_api.test_revise_price('336637869944', 5.00, sku='pokemaz-292'))"
    """
    print(f"Testing price revision: listing={listing_id!r} sku={sku!r} price=£{price:.2f}")

    if sku:
        token    = await _get_access_token()
        offer_id = await asyncio.to_thread(_get_offer_id_by_sku, sku, token)
        print(f"Offer ID from SKU: {offer_id!r}")
        if offer_id:
            result = await asyncio.to_thread(_patch_offer_price, offer_id, price, token)
            print(f"PATCH result: {result}")
            return

    result = await revise_listing_price(listing_id, price, sku=sku)
    print(f"Result: {result}")


# ---------------------------------------------------------------------------
# Policy helper — run once to find business policy IDs
# ---------------------------------------------------------------------------

async def fetch_fulfillment_policies() -> list[dict]:
    """
    Fetch fulfillment policy IDs from eBay Account API.
    Returns list of dicts with 'id', 'name', 'description' keys.
    """
    token = await _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }

    def _fetch():
        return requests.get(
            f"{_ACCOUNT_URL}/fulfillment_policy?marketplace_id=EBAY_GB",
            headers=headers,
            timeout=30,
        )

    resp = await asyncio.to_thread(_fetch)
    if resp.status_code != 200:
        raise RuntimeError(
            f"[ebay_api] Failed to fetch fulfillment policies: HTTP {resp.status_code} — {resp.text[:500]}"
        )

    policies = resp.json().get("fulfillmentPolicies", [])
    return [
        {
            "id": p.get("fulfillmentPolicyId", ""),
            "name": p.get("name", ""),
            "description": p.get("description", ""),
        }
        for p in policies
    ]


async def print_policies() -> None:
    """Fetch and print all business policy IDs for the authenticated eBay account.
 
    Run as:
        python -c "import asyncio, lister_ebay_api; asyncio.run(lister_ebay_api.print_policies())"
    """
    token   = await _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
 
    policy_endpoints = [
        ("fulfillment_policy", "Fulfillment", "fulfillmentPolicies", "fulfillmentPolicyId"),
        ("payment_policy",     "Payment",     "paymentPolicies",     "paymentPolicyId"),
        ("return_policy",      "Return",      "returnPolicies",      "returnPolicyId"),
    ]
 
    for endpoint, label, list_key, id_key in policy_endpoints:
        def _fetch(ep=endpoint):
            return requests.get(
                f"{_ACCOUNT_URL}/{ep}?marketplace_id=EBAY_GB",
                headers=headers,
                timeout=30,
            )
 
        resp = await asyncio.to_thread(_fetch)
        print(f"\n── {label} Policies (HTTP {resp.status_code}) ──")
 
        if resp.status_code != 200:
            print(f"  Error: {resp.text[:300]}")
            continue
 
        policies = resp.json().get(list_key, [])
        if not policies:
            print("  (none found — create one at ebay.co.uk → Account → Business policies)")
            continue
 
        for p in policies:
            pid  = p.get(id_key, "?")
            name = p.get("name", "?")
            desc = p.get("description", "")
            print(f"  ID:   {pid}")
            print(f"  Name: {name}")
            if desc:
                print(f"  Desc: {desc}")
            print()
 
    print("\nAdd the IDs you want to .env:")
    print("  EBAY_FULFILLMENT_POLICY_ID=<id>")
    print("  EBAY_PAYMENT_POLICY_ID=<id>")
    print("  EBAY_RETURN_POLICY_ID=<id>")