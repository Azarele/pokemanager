"""
Email notifications via Resend.
All email sending goes through this module.
"""
import os
import resend
from typing import Optional

resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "noreply@pokemanager.app")
APP_URL = os.getenv("APP_URL", "http://localhost:8000")

_ENABLED = bool(os.getenv("RESEND_API_KEY", "").startswith("re_"))


def _send(to: str, subject: str, html: str) -> bool:
    if not _ENABLED:
        logger.info(f"[email] Not configured — would send '{subject}' to {to}")
        return False
    try:
        resend.Emails.send({
            "from": FROM_EMAIL,
            "to": [to],
            "subject": subject,
            "html": html,
        })
        logger.info(f"[email] Sent '{subject}' to {to}")
        return True
    except Exception as e:
        logger.info(f"[email] Failed to send '{subject}' to {to}: {e}")
        return False


def _base_template(content: str) -> str:
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#0f0f13">
    <div style="font-family:-apple-system,BlinkMacSystemFont,Inter,sans-serif;
                max-width:520px;margin:0 auto;padding:32px 20px;background:#0f0f13;color:#e8e8f0">
        <div style="margin-bottom:24px">
            <span style="font-size:20px;font-weight:700;color:#6c63ff">Poke</span><span style="font-size:20px;font-weight:300;color:#e8e8f0">Manager</span>
        </div>
        {content}
        <div style="margin-top:32px;padding-top:20px;border-top:1px solid #2e2e3e;
                    font-size:12px;color:#888899;text-align:center">
            <a href="{APP_URL}" style="color:#6c63ff;text-decoration:none">Open PokeManager</a>
            · <a href="{APP_URL}/settings" style="color:#888899;text-decoration:none">Settings</a>
        </div>
    </div>
</body>
</html>
    """


def send_sale_notification(
    to: str,
    card_name: str,
    sell_price: float,
    profit: float,
    buyer: str = "",
    order_id: str = "",
) -> bool:
    """Send email when a card sells on eBay."""
    profit_color = "#4caf7d" if profit >= 0 else "#ff6b6b"
    profit_sign = "+" if profit >= 0 else ""

    content = f"""
    <h2 style="margin:0 0 6px;font-size:22px;color:#e8e8f0">💰 Card Sold!</h2>
    <p style="color:#888899;margin:0 0 24px">Your eBay listing sold</p>

    <div style="background:#1a1a24;border:1px solid #2e2e3e;border-radius:12px;padding:20px;margin-bottom:20px">
        <div style="font-size:16px;font-weight:600;margin-bottom:16px">{card_name}</div>
        <div style="display:flex;justify-content:space-between;margin-bottom:10px">
            <span style="color:#888899">Sale price</span>
            <span style="font-weight:600">£{sell_price:.2f}</span>
        </div>
        <div style="display:flex;justify-content:space-between;padding-top:10px;border-top:1px solid #2e2e3e">
            <span style="color:#888899">Profit</span>
            <span style="color:{profit_color};font-weight:700;font-size:18px">{profit_sign}£{profit:.2f}</span>
        </div>
    </div>

    {f'<p style="color:#888899;font-size:13px">Buyer: {buyer} · Order: {order_id}</p>' if buyer else ''}

    <a href="{APP_URL}/sales"
       style="display:inline-block;background:#6c63ff;color:white;padding:12px 24px;
              border-radius:8px;text-decoration:none;font-weight:600;margin-top:8px">
        View in PokeManager →
    </a>
    """
    return _send(to, f"Sold: {card_name} · £{sell_price:.2f}", _base_template(content))


def send_price_alert(
    to: str,
    card_name: str,
    current_price: float,
    target_price: float,
    pc_url: str = "",
) -> bool:
    """Send email when a watchlist card hits target price."""
    content = f"""
    <h2 style="margin:0 0 6px;font-size:22px;color:#e8e8f0">🔔 Price Alert</h2>
    <p style="color:#888899;margin:0 0 24px">A card on your watchlist hit your target price</p>

    <div style="background:#1a1a24;border:1px solid #2e2e3e;border-radius:12px;padding:20px;margin-bottom:20px">
        <div style="font-size:16px;font-weight:600;margin-bottom:16px">{card_name}</div>
        <div style="display:flex;justify-content:space-between;margin-bottom:10px">
            <span style="color:#888899">Current price</span>
            <span style="color:#4caf7d;font-weight:700">£{current_price:.2f}</span>
        </div>
        <div style="display:flex;justify-content:space-between">
            <span style="color:#888899">Your target</span>
            <span>£{target_price:.2f}</span>
        </div>
    </div>

    <a href="{APP_URL}/watchlist"
       style="display:inline-block;background:#6c63ff;color:white;padding:12px 24px;
              border-radius:8px;text-decoration:none;font-weight:600">
        View Watchlist →
    </a>
    """
    return _send(to, f"Price Alert: {card_name} is at £{current_price:.2f}", _base_template(content))


def send_welcome(to: str, display_name: str) -> bool:
    """Send welcome email to new users."""
    content = f"""
    <h2 style="margin:0 0 6px;font-size:22px;color:#e8e8f0">Welcome to PokeManager! 🃏</h2>
    <p style="color:#888899;margin:0 0 24px">Hi {display_name}, your account is ready.</p>

    <div style="background:#1a1a24;border:1px solid #2e2e3e;border-radius:12px;padding:20px;margin-bottom:20px">
        <div style="margin-bottom:12px">
            <span style="color:#4caf7d">✅</span>
            <span style="margin-left:8px">Add cards from PriceCharting URLs</span>
        </div>
        <div style="margin-bottom:12px">
            <span style="color:#4caf7d">✅</span>
            <span style="margin-left:8px">Track market prices automatically</span>
        </div>
        <div style="margin-bottom:12px">
            <span style="color:#4caf7d">✅</span>
            <span style="margin-left:8px">Analyse your profit and ROI</span>
        </div>
        <div>
            <span style="color:#6c63ff">⭐</span>
            <span style="margin-left:8px">Upgrade to list on eBay and Vinted</span>
        </div>
    </div>

    <a href="{APP_URL}"
       style="display:inline-block;background:#6c63ff;color:white;padding:12px 24px;
              border-radius:8px;text-decoration:none;font-weight:600">
        Get Started →
    </a>
    """
    return _send(to, "Welcome to PokeManager!", _base_template(content))
