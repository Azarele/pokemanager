"""
Discord webhook notifications for PokeManager events.
Sends embeds to user-configured Discord webhooks.
"""
import httpx
from datetime import datetime
from web.database import get_db


async def send_discord_notification(
    user_id: str,
    title: str,
    message: str,
    color: int,
) -> None:
    """
    Send a Discord embed notification to user's webhook.

    Args:
        user_id: User's Supabase ID
        title: Embed title
        message: Embed description
        color: Embed color (int). Common: 5763719=green, 15548997=red, 16776960=yellow

    Silently fails if no webhook is set or request fails.
    """
    try:
        db = get_db()
        profile = db.table("user_profiles").select("discord_webhook_url").eq(
            "id", user_id
        ).single().execute()

        webhook_url = profile.data.get("discord_webhook_url") if profile.data else None
        if not webhook_url:
            return

        embed = {
            "embeds": [{
                "title": title,
                "description": message,
                "color": color,
                "footer": {"text": "PokeManager"},
                "timestamp": datetime.utcnow().isoformat(),
            }]
        }

        async with httpx.AsyncClient() as client:
            await client.post(webhook_url, json=embed, timeout=5.0)
    except Exception:
        pass
