import re
from datetime import datetime
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup
from fastapi import APIRouter, Depends
from pydantic import BaseModel

import scraper
from web import db_inventory as db
from web.auth import get_current_user

router = APIRouter()


def _extract_card_number(text: str) -> Optional[str]:
    """Return the normalised card number (leading zeros stripped) from a title."""
    patterns = [
        r'#?(\d{2,3})/\d{2,3}',       # 160/132  094/080
        r'#(\d{2,3})\b',               # #160
        r'\bcard[:\s]+(\d{2,3})\b',    # card 160
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(1).lstrip('0') or '0'
    return None


def _pc_url(href: str) -> str:
    return href if href.startswith("http") else "https://www.pricecharting.com" + href


class AnalyseRequest(BaseModel):
    url: str
    asking_price: Optional[float] = None
    pc_url_override: Optional[str] = None


@router.post("/analyse")
async def analyse_listing(req: AnalyseRequest, user: dict = Depends(get_current_user)):
    url             = req.url.strip()
    asking_override = req.asking_price or 0.0
    pc_override     = (req.pc_url_override or "").strip()

    result: dict = {
        "url":              url,
        "platform":         "unknown",
        "listing_title":    None,
        "asking_price":     None,
        "pc_url":           None,
        "pc_name":          None,
        "market_price":     None,
        "expected_profit":  None,
        "roi_pct":          None,
        "margin_pct":       None,
        "avg_days_to_sell": None,
        "similar_sold":     None,
        "match_confidence": "high",
        "verdict":          None,
        "error":            None,
    }

    if "ebay.co.uk" in url or "ebay.com" in url:
        result["platform"] = "ebay"
    elif "vinted.co.uk" in url or "vinted.com" in url:
        result["platform"] = "vinted"
    else:
        result["platform"] = "other"

    # ── Scrape listing page ──────────────────────────────────────────────────
    try:
        ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=ua, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")

        if result["platform"] == "ebay":
            title_el = soup.select_one("h1.x-item-title__mainTitle span, h1[itemprop='name']")
            result["listing_title"] = title_el.get_text(strip=True) if title_el else None
            price_el = soup.select_one(".x-price-primary span[itemprop='price'], [itemprop='price']")
            if price_el:
                raw = price_el.get("content") or price_el.get_text(strip=True)
                m = re.search(r"[\d]+\.?\d*", raw.replace(",", ""))
                if m:
                    result["asking_price"] = float(m.group())
        elif result["platform"] == "vinted":
            title_el = soup.select_one("h1, [data-testid='item-title']")
            result["listing_title"] = title_el.get_text(strip=True) if title_el else None
            price_el = soup.select_one("[data-testid='item-price']")
            if price_el:
                m = re.search(r"[\d.]+", price_el.get_text())
                if m:
                    result["asking_price"] = float(m.group())

    except Exception as e:
        result["error"] = f"Could not scrape listing: {e}"

    if asking_override > 0:
        result["asking_price"] = asking_override

    if not result["asking_price"]:
        result["error"] = result["error"] or "Could not determine asking price — enter it manually"
        return result

    # ── PC URL override: skip search entirely ────────────────────────────────
    if pc_override and pc_override.startswith("https://www.pricecharting.com"):
        result["pc_url"] = pc_override
        result["pc_name"] = pc_override.rstrip("/").split("/")[-1].replace("-", " ").title()
        try:
            card_name, market_price = await scraper.scrape_card(pc_override, "Near mint or better")
            if market_price:
                result["market_price"] = market_price
            if card_name:
                result["pc_name"] = card_name
        except Exception as e:
            result["error"] = f"PriceCharting scrape failed: {e}"
    else:
        # ── PriceCharting search ─────────────────────────────────────────────
        title = result["listing_title"] or ""
        if title:
            try:
                listing_card_num = _extract_card_number(title)

                # Meaningful words + card number for a tighter query
                words = re.sub(r"[^\w\s]", " ", title).split()
                clean_words = [w for w in words if len(w) > 2 and not w.isdigit()][:5]
                search_q = " ".join(clean_words)
                if listing_card_num:
                    search_q = f"{search_q} {listing_card_num}"

                pc_search = (
                    "https://www.pricecharting.com/search-products"
                    f"?q={search_q.replace(' ', '+')}&type=prices"
                )

                ua = {"User-Agent": "Mozilla/5.0"}
                async with aiohttp.ClientSession() as session:
                    async with session.get(pc_search, headers=ua, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        pc_html = await resp.text()

                pc_soup = BeautifulSoup(pc_html, "html.parser")
                rows = pc_soup.select("table#games_table tbody tr")

                best_href: Optional[str] = None
                best_name: Optional[str] = None
                validated = False

                for row in rows[:5]:
                    link = row.select_one("td a")
                    if not link:
                        continue
                    href    = link.get("href") or ""
                    pc_name = link.get_text(strip=True)

                    if listing_card_num:
                        # URL slug ends with -<number>
                        slug_m = re.search(r"-(\d+)$", href.rstrip("/"))
                        slug_num = slug_m.group(1).lstrip("0") if slug_m else None

                        if slug_num == listing_card_num:
                            best_href, best_name, validated = href, pc_name, True
                            break
                        if listing_card_num in pc_name:
                            best_href, best_name, validated = href, pc_name, True
                            break
                    else:
                        best_href, best_name, validated = href, pc_name, True
                        break

                # Fallback to first result with low confidence
                if not best_href and rows:
                    link = rows[0].select_one("td a")
                    if link:
                        best_href = link.get("href") or ""
                        best_name = link.get_text(strip=True)
                        validated = False

                if best_href:
                    result["pc_url"]  = _pc_url(best_href)
                    result["pc_name"] = best_name
                    if not validated:
                        result["match_confidence"] = "low"

                    card_name, market_price = await scraper.scrape_card(
                        result["pc_url"], "Near mint or better"
                    )
                    if market_price:
                        result["market_price"] = market_price
                    if card_name and not result["pc_name"]:
                        result["pc_name"] = card_name

            except Exception as e:
                result["error"] = f"PriceCharting lookup failed: {e}"

    # ── Profit maths ─────────────────────────────────────────────────────────
    if result["market_price"] and result["asking_price"]:
        asking   = result["asking_price"]
        market   = result["market_price"]
        ebay_fee = market * 0.1235
        net      = market - ebay_fee - 1.50
        profit   = net - asking

        result["expected_profit"] = round(profit, 2)
        result["roi_pct"]         = round((profit / asking * 100) if asking else 0, 1)
        result["margin_pct"]      = round((profit / market * 100) if market else 0, 1)

        if profit > 10:
            result["verdict"] = "strong_buy"
        elif profit > 3:
            result["verdict"] = "buy"
        elif profit > 0:
            result["verdict"] = "marginal"
        else:
            result["verdict"] = "pass"

    # ── Velocity from local sold history ─────────────────────────────────────
    try:
        search_title = result["pc_name"] or (result["listing_title"] or "")
        keywords = [w for w in search_title.split() if len(w) > 4]
        if keywords:
            all_sold = await db.get_all_items(user["id"], status_filter="Sold")
            similar = [
                i for i in all_sold
                if any(kw.lower() in str(i.get("card_name", "")).lower() for kw in keywords)
            ]
            if similar:
                days_list = []
                for item in similar:
                    try:
                        added = datetime.strptime(str(item.get("date_added", ""))[:10], "%Y-%m-%d")
                        sold  = datetime.strptime(str(item.get("date_sold",  ""))[:10], "%Y-%m-%d")
                        days_list.append(max((sold - added).days, 1))
                    except Exception:
                        pass
                if days_list:
                    result["avg_days_to_sell"] = round(sum(days_list) / len(days_list), 1)
                    result["similar_sold"]     = len(similar)
    except Exception:
        pass

    return result
