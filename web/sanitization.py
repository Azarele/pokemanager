"""
Input sanitization utilities to prevent XSS and injection attacks.
Strips HTML, script tags, and other dangerous content from user inputs.
"""
import re
import logging

logger = logging.getLogger(__name__)


def sanitize_text(text: str | None, max_length: int = 500) -> str:
    """
    Sanitize text input by:
    1. Removing HTML/script tags
    2. Removing dangerous protocols (javascript:, data:, etc)
    3. Truncating to max length
    4. Stripping whitespace
    """
    if not text:
        return ""

    text = str(text).strip()

    # Remove HTML/XML tags
    text = re.sub(r'<[^>]+>', '', text)

    # Remove script-like content
    text = re.sub(r'(?i)javascript:', '', text)
    text = re.sub(r'(?i)on\w+\s*=', '', text)
    text = re.sub(r'(?i)<script[^>]*>.*?</script>', '', text)

    # Remove other dangerous protocols
    for protocol in ['data:', 'vbscript:', 'file:']:
        text = re.sub(rf'(?i){protocol}', '', text)

    # Truncate to max length
    if len(text) > max_length:
        text = text[:max_length]

    return text.strip()


def sanitize_url(url: str | None) -> str | None:
    """
    Validate and sanitize URLs.
    Only allows http:// and https:// protocols.
    """
    if not url:
        return None

    url = str(url).strip()

    # Only allow http and https
    if not url.lower().startswith(('http://', 'https://')):
        return None

    # Basic URL validation
    if len(url) > 2000:
        return None

    return url


def sanitize_field(field_name: str, value: str | None) -> str:
    """
    Sanitize a specific field based on its type.
    """
    if not value:
        return ""

    if field_name.lower() in ('url', 'pc_url', 'ebay_url', 'webhook_url'):
        return sanitize_url(value) or ""

    if field_name.lower() in ('notes', 'description', 'card_name', 'display_name', 'traded_item_names'):
        return sanitize_text(value)

    return sanitize_text(value)
