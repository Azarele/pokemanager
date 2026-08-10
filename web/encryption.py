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
    """Decrypt a sensitive value. Returns None if value is None or decryption fails."""
    if not encrypted_value or not cipher:
        return encrypted_value
    try:
        return cipher.decrypt(encrypted_value.encode()).decode()
    except InvalidToken:
        logger.warning("Invalid encryption token — returning empty string")
        return None
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return None


def mask_credential(value: str | None, show_chars: int = 4) -> str | None:
    """Mask a credential showing only last N characters. E.g., 'abcd1234' -> '****1234'"""
    if not value:
        return None
    if len(value) <= show_chars:
        return f"{'*' * show_chars}"
    return f"{'*' * (len(value) - show_chars)}{value[-show_chars:]}"
