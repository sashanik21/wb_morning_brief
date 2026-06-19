"""Supabase client factory for the Streamlit dashboard."""

import os
from functools import lru_cache

import streamlit as st
from supabase import create_client


def _secret_value(name):
    """Read a Streamlit secret without failing outside Streamlit Cloud."""
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value).strip() if value is not None else ""


def _first_present(*values):
    for value in values:
        stripped = (value or "").strip()
        if stripped:
            return stripped
    return ""


@lru_cache(maxsize=1)
def get_supabase_client():
    """Create a cached read-only Supabase client from environment variables or Streamlit Secrets."""
    supabase_url = _first_present(
        os.getenv("SUPABASE_URL"),
        _secret_value("SUPABASE_URL"),
    ).rstrip("/")
    supabase_key = _first_present(
        os.getenv("SUPABASE_KEY"),
        os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        os.getenv("SUPABASE_ANON_KEY"),
        _secret_value("SUPABASE_KEY"),
        _secret_value("SUPABASE_SERVICE_ROLE_KEY"),
        _secret_value("SUPABASE_ANON_KEY"),
    )

    if not supabase_url or not supabase_key:
        message = (
            "Supabase credentials are not configured. Set SUPABASE_URL and one of "
            "SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY, or SUPABASE_ANON_KEY in "
            "environment variables or Streamlit Secrets."
        )
        st.error(message)
        raise RuntimeError(message)

    return create_client(supabase_url, supabase_key)
