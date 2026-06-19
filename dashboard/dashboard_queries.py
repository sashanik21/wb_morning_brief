"""Read-only Supabase queries for the Executive Dashboard."""

import sys
from pathlib import Path
from datetime import date, datetime, timedelta

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

import pandas as pd
import streamlit as st

from supabase_client import get_supabase_client, get_supabase_credentials_info


ROW_LIMIT = 10000
REPORT_DATES_PAGE_SIZE = 1000
REPORT_DATE_WITH_CREATED_AT_FALLBACK = "report_date,created_at"


def _execute(query):
    response = query.execute()
    return response.data or []


def _sanitize_error(error):
    text = str(error or "").replace("\n", " ").strip()
    if not text:
        return ""
    for marker in ("eyJ", "sb_secret_", "sb_publishable_"):
        if marker in text:
            text = text.split(marker, 1)[0] + "[redacted]"
    return text[:300]


def _try_execute(query):
    try:
        return _execute(query), True, ""
    except Exception as error:
        return [], False, _sanitize_error(error)


def _safe_execute(query):
    rows, _, _ = _try_execute(query)
    return rows


def _count_table_rows(client, table_name):
    try:
        response = client.table(table_name).select("*", count="exact", head=True).execute()
        return response.count or 0, True, ""
    except Exception as error:
        rows, succeeded, row_error = _try_execute(client.table(table_name).select("*").limit(1))
        if succeeded:
            return len(rows), True, ""
        return 0, False, _sanitize_error(error) or row_error


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
    if date_field == REPORT_DATE_WITH_CREATED_AT_FALLBACK:
        return query.eq("report_date", report_day)
    return query.eq(date_field, report_day)


def _row_matches_report_day(row, report_day):
    return (_normalize_date(row.get("report_date")) or _normalize_date(row.get("created_at"))) == report_day


def _execute_with_optional_report_date(query_factory, report_date=None):
    if not report_date:
        return _safe_execute(query_factory())

    rows, succeeded, _ = _try_execute(query_factory().eq("report_date", str(report_date)))
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
    dates = set()
    offset = 0

    while True:
        rows, succeeded, _ = _try_execute(
            client.table("problems")
            .select(REPORT_DATE_WITH_CREATED_AT_FALLBACK)
            .order("report_date", desc=True)
            .order("created_at", desc=True)
            .range(offset, offset + REPORT_DATES_PAGE_SIZE - 1)
        )
        if not succeeded:
            break

        for row in rows:
            report_day = _normalize_date(row.get("report_date")) or _normalize_date(row.get("created_at"))
            if report_day:
                dates.add(report_day)

        if len(rows) < REPORT_DATES_PAGE_SIZE:
            return sorted(dates, reverse=True), REPORT_DATE_WITH_CREATED_AT_FALLBACK
        offset += REPORT_DATES_PAGE_SIZE

    dates = set()
    offset = 0
    while True:
        rows, succeeded, _ = _try_execute(
            client.table("problems")
            .select("created_at")
            .range(offset, offset + REPORT_DATES_PAGE_SIZE - 1)
        )
        if not succeeded:
            return [], "report_date"

        for row in rows:
            report_day = _normalize_date(row.get("created_at"))
            if report_day:
                dates.add(report_day)

        if len(rows) < REPORT_DATES_PAGE_SIZE:
            return sorted(dates, reverse=True), "created_at"
        offset += REPORT_DATES_PAGE_SIZE


@st.cache_data(ttl=300)
def fetch_problems(report_date=None, seller_id=None, reason=None, limit=ROW_LIMIT, date_field="report_date"):
    client = get_supabase_client()
    report_day = _normalize_date(report_date)
    query = client.table("problems").select("*")
    query = _apply_problem_date_filter(query, report_date=report_day, date_field=date_field)
    if seller_id and seller_id != "Все продавцы":
        query = query.eq("seller_id", seller_id)
    rows = _safe_execute(query.limit(limit))

    if report_day and date_field == REPORT_DATE_WITH_CREATED_AT_FALLBACK:
        created_at_query = (
            client.table("problems")
            .select("*")
            .gte("created_at", report_day)
            .lt("created_at", _next_date(report_day))
        )
        if seller_id and seller_id != "Все продавцы":
            created_at_query = created_at_query.eq("seller_id", seller_id)
        rows_by_key = {}
        for row in [*rows, *_safe_execute(created_at_query.limit(limit))]:
            if _row_matches_report_day(row, report_day):
                key = row.get("id") or repr(sorted(row.items()))
                rows_by_key[key] = row
        rows = list(rows_by_key.values())[:limit]

    if reason and reason != "Все причины":
        rows = [row for row in rows if reason in {row.get("root_cause"), row.get("problem_label"), row.get("problem_type"), row.get("decline_source"), row.get("metric")}]
    return rows


@st.cache_data(ttl=300)
def check_dashboard_connection():
    """Check dashboard Supabase access and return safe diagnostics."""
    credentials = get_supabase_credentials_info()
    diagnostics = {
        "supabase_connected": False,
        "sellers_readable": False,
        "problems_readable": False,
        "api_coverage_daily_readable": False,
        "api_coverage_daily_available": False,
        "problems_total_count": 0,
        "last_query_error": "",
        "credentials_source": credentials["credentials_source"],
        "key_type": credentials["key_type"],
    }

    if not credentials["url_configured"] or not credentials["key_configured"]:
        diagnostics["last_query_error"] = "Supabase credentials are not configured"
        return diagnostics

    try:
        client = get_supabase_client()
        diagnostics["supabase_connected"] = True
    except Exception as error:
        diagnostics["last_query_error"] = _sanitize_error(error)
        return diagnostics

    checks = (
        ("sellers", "sellers_readable"),
        ("problems", "problems_readable"),
        ("api_coverage_daily", "api_coverage_daily_readable"),
    )
    for table_name, key in checks:
        _, succeeded, error = _try_execute(client.table(table_name).select("*").limit(1))
        diagnostics[key] = succeeded
        if table_name == "api_coverage_daily":
            diagnostics["api_coverage_daily_available"] = succeeded
        if error:
            diagnostics["last_query_error"] = error

    count, count_succeeded, count_error = _count_table_rows(client, "problems")
    diagnostics["problems_total_count"] = count
    diagnostics["problems_readable"] = diagnostics["problems_readable"] and count_succeeded
    if count_error:
        diagnostics["last_query_error"] = count_error
    return diagnostics


@st.cache_data(ttl=300)
def fetch_problems_diagnostics(report_date=None, date_field="report_date", available_dates=None, loaded_rows=None):
    """Return safe debug counters for problems loading."""
    connection = check_dashboard_connection()
    if loaded_rows is None:
        loaded_rows = len(fetch_problems(report_date=report_date, date_field=date_field, limit=ROW_LIMIT))
    return {
        "total_rows_before_date_filter": connection["problems_total_count"],
        "rows_loaded_after_date_filter": loaded_rows,
        "date_field_used": date_field,
        "selected_date": _normalize_date(report_date),
        "available_dates_count": len(available_dates or []),
        "available_dates_sample": (available_dates or [])[:10],
        **connection,
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


def _row_nm_id(row):
    return row.get("nm_id") or row.get("nmId") or row.get("nmID")


def _row_seller_id(row):
    return row.get("seller_id") or row.get("sellerId")


@st.cache_data(ttl=300)
def fetch_sku_options(seller_id=None):
    """Return SKU options from products, falling back to problems when products is empty."""
    client = get_supabase_client()
    product_rows = _safe_execute(client.table("products").select("*").limit(ROW_LIMIT))
    if seller_id and seller_id != "Все продавцы":
        product_rows = [row for row in product_rows if str(_row_seller_id(row)) == str(seller_id)]

    options_by_nm_id = {}
    for row in product_rows:
        nm_id = _row_nm_id(row)
        if nm_id in (None, ""):
            continue
        options_by_nm_id[str(nm_id)] = {
            "nm_id": str(nm_id),
            "title": row.get("title") or row.get("productName") or row.get("product_name") or "",
            "seller_id": _row_seller_id(row),
            "vendor_code": row.get("vendor_code") or row.get("vendorCode") or row.get("supplier_article") or row.get("supplierArticle") or "",
        }

    if options_by_nm_id:
        return sorted(options_by_nm_id.values(), key=lambda row: (row.get("title") or "", row["nm_id"]))

    problem_rows = _safe_execute(client.table("problems").select("*").limit(ROW_LIMIT))
    if seller_id and seller_id != "Все продавцы":
        problem_rows = [row for row in problem_rows if str(_row_seller_id(row)) == str(seller_id)]
    for row in problem_rows:
        nm_id = _row_nm_id(row)
        if nm_id in (None, ""):
            continue
        options_by_nm_id.setdefault(
            str(nm_id),
            {
                "nm_id": str(nm_id),
                "title": row.get("title") or row.get("productName") or row.get("product_name") or "",
                "seller_id": _row_seller_id(row),
                "vendor_code": row.get("vendor_code") or row.get("vendorCode") or row.get("supplier_article") or row.get("supplierArticle") or "",
            },
        )
    return sorted(options_by_nm_id.values(), key=lambda row: (row.get("title") or "", row["nm_id"]))


@st.cache_data(ttl=300)
def fetch_sku_history(nm_id, seller_id=None):
    """Return daily_funnel rows for one SKU without assuming exact column names."""
    client = get_supabase_client()
    rows = []
    for nm_field in ("nm_id", "nmId", "nmID"):
        query = client.table("daily_funnel").select("*").eq(nm_field, nm_id)
        candidate_rows, succeeded, _ = _try_execute(query.limit(ROW_LIMIT))
        if succeeded:
            rows = candidate_rows
            break
    if seller_id and seller_id != "Все продавцы":
        rows = [row for row in rows if str(_row_seller_id(row)) in ("None", str(seller_id)) or _row_seller_id(row) in (None, "")]
    return rows


@st.cache_data(ttl=300)
def fetch_sku_problems(nm_id, seller_id=None):
    """Return problems rows for one SKU without assuming exact column names."""
    client = get_supabase_client()
    rows = []
    for nm_field in ("nm_id", "nmId", "nmID"):
        query = client.table("problems").select("*").eq(nm_field, nm_id)
        candidate_rows, succeeded, _ = _try_execute(query.limit(ROW_LIMIT))
        if succeeded:
            rows = candidate_rows
            break
    if seller_id and seller_id != "Все продавцы":
        rows = [row for row in rows if str(_row_seller_id(row)) in ("None", str(seller_id)) or _row_seller_id(row) in (None, "")]
    return rows
