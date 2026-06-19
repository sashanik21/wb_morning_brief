"""Supabase client factory for the Streamlit dashboard."""

import os
from functools import lru_cache

from supabase import create_client


@lru_cache(maxsize=1)
def get_supabase_client():
    """Create a cached read-only Supabase client from environment variables."""
    supabase_url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    supabase_key = (
        os.getenv("SUPABASE_KEY")
        or os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    ).strip()

    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "Set SUPABASE_URL and SUPABASE_KEY environment variables before launching the dashboard."
        )

    return create_client(supabase_url, supabase_key)
