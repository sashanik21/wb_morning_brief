"""Supabase client factory for the Streamlit dashboard."""

import os
from functools import lru_cache

import streamlit as st
from supabase import create_client


_SUPABASE_KEY_NAMES = (
    "SUPABASE_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "SUPABASE_ANON_KEY",
)


def _secret_value(name):
    """Read a Streamlit secret without failing outside Streamlit Cloud."""
    try:
        value = st.secrets.get(name, "")
    except Exception:
        value = ""
    return str(value).strip() if value is not None else ""


def _first_present_with_source(*candidates):
    for value, source, name in candidates:
        stripped = (value or "").strip()
        if stripped:
            return stripped, source, name
    return "", "missing", ""


def _key_type(key_name, key_value):
    name = (key_name or "").upper()
    key = (key_value or "").strip()
    if "SERVICE_ROLE" in name:
        return "service_role"
    if "ANON" in name:
        return "anon"
    if key.startswith("sb_secret_"):
        return "service_role"
    if key.startswith("sb_publishable_"):
        return "publishable"
    if key.count(".") == 2:
        return "unknown"
    return "unknown"


def get_supabase_credentials_info():
    """Return safe Supabase credential metadata without exposing secrets."""
    supabase_url, url_source, _ = _first_present_with_source(
        (os.getenv("SUPABASE_URL"), "env", "SUPABASE_URL"),
        (_secret_value("SUPABASE_URL"), "streamlit secrets", "SUPABASE_URL"),
    )
    key_candidates = []
    for name in _SUPABASE_KEY_NAMES:
        key_candidates.append((os.getenv(name), "env", name))
    for name in _SUPABASE_KEY_NAMES:
        key_candidates.append((_secret_value(name), "streamlit secrets", name))
    supabase_key, key_source, key_name = _first_present_with_source(*key_candidates)
    credential_source = key_source if key_source != "missing" else url_source
    return {
        "url_configured": bool(supabase_url),
        "key_configured": bool(supabase_key),
        "credentials_source": credential_source,
        "key_type": _key_type(key_name, supabase_key),
        "supabase_url": supabase_url.rstrip("/"),
        "supabase_key": supabase_key,
    }


@lru_cache(maxsize=1)
def get_supabase_client():
    """Create a cached read-only Supabase client from environment variables or Streamlit Secrets."""
    credentials = get_supabase_credentials_info()
    supabase_url = credentials["supabase_url"]
    supabase_key = credentials["supabase_key"]

    if not supabase_url or not supabase_key:
        message = (
            "Supabase credentials are not configured. Set SUPABASE_URL and one of "
            "SUPABASE_KEY, SUPABASE_SERVICE_ROLE_KEY, or SUPABASE_ANON_KEY in "
            "environment variables or Streamlit Secrets."
        )
        st.error(message)
        raise RuntimeError(message)

    return create_client(supabase_url, supabase_key)
