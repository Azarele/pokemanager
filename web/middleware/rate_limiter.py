"""
Per-endpoint rate limiting using slowapi.
Configured separately for different endpoint types.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

# Rate limit configurations
LIMITS = {
    "auth_login": "5/minute",        # 5 attempts per minute per IP
    "auth_register": "3/minute",     # 3 registrations per minute per IP
    "auth_refresh": "20/minute",     # More generous for refresh
    "general": "100/minute",         # General API calls
}
