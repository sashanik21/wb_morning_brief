"""Read-only Supabase queries for the Executive Dashboard."""

from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from supabase_client import get_supabase_client


ROW_LIMIT = 10000
PROBLEM_DATE_FIELDS = ("report_date", "date", "created_at", "selected_date", "period_date")


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


def _normalize_date(value):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _next_date(report_date):
    parsed = datetime.strptime(str(report_date)[:10], "%Y-%m-%d").date()
    return (parsed + timedelta(days=1)).isoformat()


def _apply_problem_date_filter(query, report_date=None, date_field=None):
    if not report_date or not date_field:
        return query
    report_day = _normalize_date(report_date)
    if not report_day:
        return query
    if date_field == "created_at":
        return query.gte(date_field, report_day).lt(date_field, _next_date(report_day))
    return query.eq(date_field, report_day)


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
    for field in PROBLEM_DATE_FIELDS:
        rows, succeeded = _try_execute(
            client.table("problems")
            .select(field)
            .order(field, desc=True)
            .limit(ROW_LIMIT)
        )
        if not succeeded:
            continue
        dates = sorted({_normalize_date(row.get(field)) for row in rows if _normalize_date(row.get(field))}, reverse=True)
        if dates:
            return dates, field
    return [date.today().isoformat()], "report_date"


@st.cache_data(ttl=300)
def fetch_problems(report_date=None, seller_id=None, reason=None, limit=ROW_LIMIT, date_field="report_date"):
    client = get_supabase_client()
    query = client.table("problems").select("*")
    query = _apply_problem_date_filter(query, report_date=report_date, date_field=date_field)
    if seller_id and seller_id != "Все продавцы":
        query = query.eq("seller_id", seller_id)
    rows = _safe_execute(query.limit(limit))
    if reason and reason != "Все причины":
        rows = [row for row in rows if reason in {row.get("root_cause"), row.get("problem_label"), row.get("problem_type"), row.get("decline_source"), row.get("metric")}]
    return rows


@st.cache_data(ttl=300)
def fetch_problems_diagnostics(report_date=None, date_field="report_date", available_dates=None, loaded_rows=None):
    """Return safe debug counters for problems loading."""
    total_rows = len(fetch_problems(date_field=date_field, limit=ROW_LIMIT))
    if loaded_rows is None:
        loaded_rows = len(fetch_problems(report_date=report_date, date_field=date_field, limit=ROW_LIMIT))
    return {
        "total_rows_before_date_filter": total_rows,
        "rows_loaded_after_date_filter": loaded_rows,
        "date_field_used": date_field,
        "selected_date": _normalize_date(report_date),
        "available_dates_count": len(available_dates or []),
    }


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
