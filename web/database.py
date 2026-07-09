"""
Supabase database client.
All database operations go through this module.
Uses the service-role key so RLS is enforced at the query level
by passing user_id explicitly — never trust the client to scope its own rows.
"""
import os
from typing import Optional

from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

_url = os.getenv("SUPABASE_URL", "")
_key = os.getenv("SUPABASE_SERVICE_KEY", "")

supabase: Optional[Client] = None

if not _url or not _key:
    print("[db] Warning: SUPABASE_URL or SUPABASE_SERVICE_KEY not set — database features disabled")
else:
    try:
        supabase = create_client(_url, _key)
    except Exception as exc:
        print(f"[db] Failed to initialise Supabase client: {exc}")


def get_db() -> Client:
    if supabase is None:
        raise RuntimeError(
            "Database not configured — set SUPABASE_URL and SUPABASE_SERVICE_KEY in .env"
        )
    return supabase


def is_configured() -> bool:
    return supabase is not None
