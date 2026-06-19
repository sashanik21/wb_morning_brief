"""Read-only Supabase queries for the Executive Dashboard."""

from datetime import date

import pandas as pd
import streamlit as st

from supabase_client import get_supabase_client


ROW_LIMIT = 10000


def _execute(query):
    response = query.execute()
    return response.data or []


def _try_execute(query):
    try:
        return _execute(query), True
    except Exception:
        return [], False


def _safe_execute(query):
    rows, _ = _try_execute(query)
    return rows


def _execute_with_optional_report_date(query_factory, report_date=None):
    if not report_date:
        return _safe_execute(query_factory())

    rows, succeeded = _try_execute(query_factory().eq("report_date", str(report_date)))
    if succeeded:
        return rows
    return _safe_execute(query_factory())


@st.cache_data(ttl=300)
def fetch_sellers():
    client = get_supabase_client()
    rows = _safe_execute(client.table("sellers").select("*").limit(ROW_LIMIT))
    sellers_by_id = {}
    for row in rows:
        seller_id = row.get("seller_id") or row.get("id")
        name = row.get("seller_name") or row.get("name") or row.get("title")
        if seller_id not in (None, ""):
            sellers_by_id[str(seller_id)] = name or f"seller_id={seller_id}"
    return rows, sellers_by_id


@st.cache_data(ttl=300)
def fetch_report_dates():
    client = get_supabase_client()
    rows = _safe_execute(
        client.table("problems")
        .select("report_date")
        .order("report_date", desc=True)
        .limit(ROW_LIMIT)
    )
    dates = sorted({row.get("report_date") for row in rows if row.get("report_date")}, reverse=True)
    return dates or [date.today().isoformat()]


@st.cache_data(ttl=300)
def fetch_problems(report_date=None, seller_id=None, reason=None, limit=ROW_LIMIT):
    client = get_supabase_client()
    query = client.table("problems").select("*")
    if report_date:
        query = query.eq("report_date", str(report_date))
    if seller_id and seller_id != "Все продавцы":
        query = query.eq("seller_id", seller_id)
    rows = _safe_execute(query.limit(limit))
    if reason and reason != "Все причины":
        rows = [row for row in rows if reason in {row.get("root_cause"), row.get("problem_label"), row.get("problem_type"), row.get("decline_source"), row.get("metric")}]
    return rows


@st.cache_data(ttl=300)
def fetch_data_quality(report_date=None):
    client = get_supabase_client()
    quality = {
        "problems_without_seller_id": 0,
        "ads_bid_history_without_seller_id": 0,
        "sku_without_ads": 0,
        "sku_without_supplies": 0,
    }

    problems_query = lambda: client.table("problems").select("seller_id,report_date").limit(ROW_LIMIT)
    problems = _execute_with_optional_report_date(problems_query, report_date)
    quality["problems_without_seller_id"] = sum(1 for row in problems if not row.get("seller_id"))

    ads_query = lambda: client.table("ads_bid_history").select("seller_id,report_date").limit(ROW_LIMIT)
    ads_rows = _execute_with_optional_report_date(ads_query, report_date)
    quality["ads_bid_history_without_seller_id"] = sum(1 for row in ads_rows if not row.get("seller_id"))

    coverage_query = lambda: client.table("api_coverage_daily").select("*").limit(ROW_LIMIT)
    coverage = _execute_with_optional_report_date(coverage_query, report_date)

    if coverage:
        quality["sku_without_ads"] = sum(1 for row in coverage if not row.get("in_ads_api"))
        quality["sku_without_supplies"] = sum(1 for row in coverage if not row.get("in_supplies_api"))

    return quality


def unique_reasons(problems):
    values = set()
    for row in problems:
        for key in ("root_cause", "problem_label", "problem_type", "decline_source", "metric"):
            if row.get(key):
                values.add(row[key])
    return ["Все причины", *sorted(values)]


def dataframe_for_display(dataframe):
    if dataframe.empty:
        return dataframe
    return dataframe.reset_index(drop=True)
