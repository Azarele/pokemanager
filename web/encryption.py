"""
Encryption utilities for storing sensitive credentials at rest.
Uses Fernet (AES-128-CBC with HMAC) for authenticated encryption.
"""
import os
import logging
from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)

ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    logger.warning("ENCRYPTION_KEY not set — sensitive data will not be encrypted")
    ENCRYPTION_KEY = None

cipher = Fernet(ENCRYPTION_KEY) if ENCRYPTION_KEY else None


def encrypt(value: str | None) -> str | None:
    """Encrypt a sensitive value. Returns None if value is None or encryption disabled."""
    if not value or not cipher:
        return value
    try:
        return cipher.encrypt(value.encode()).decode()
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        return value


def decrypt(encrypted_value: str | None) -> str | None:
    """
    Decrypt a sensitive value. Gracefully handles both encrypted and plain text values.
    Returns encrypted_value as-is if decryption fails (assumes plain text from pre-encryption).
    """
    if not encrypted_value or not cipher:
        return encrypted_value
    try:
        return cipher.decrypt(encrypted_value.encode()).decode()
    except (InvalidToken, Exception):
        # Value is plain text (pre-encryption migration) or cipher is disabled
        # Return as-is — it will get encrypted next time user saves settings
        logger.debug(f"Treating value as plain text (pre-encryption or cipher disabled)")
        return encrypted_value


def mask_credential(value: str | None, show_chars: int = 4) -> str | None:
    """Mask a credential showing only last N characters. E.g., 'abcd1234' -> '****1234'"""
    if not value:
        return None
    if len(value) <= show_chars:
        return f"{'*' * show_chars}"
    return f"{'*' * (len(value) - show_chars)}{value[-show_chars:]}"
