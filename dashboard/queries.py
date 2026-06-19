"""Read-only Supabase queries for the Executive Dashboard."""

from datetime import date

import pandas as pd

from dashboard.supabase_client import get_supabase_client


ROW_LIMIT = 10000


def _execute(query):
    response = query.execute()
    return response.data or []


def fetch_sellers():
    client = get_supabase_client()
    rows = _execute(client.table("sellers").select("*").limit(ROW_LIMIT))
    sellers_by_id = {}
    for row in rows:
        seller_id = row.get("seller_id") or row.get("id")
        name = row.get("seller_name") or row.get("name") or row.get("title")
        if seller_id not in (None, ""):
            sellers_by_id[str(seller_id)] = name or f"seller_id={seller_id}"
    return rows, sellers_by_id


def fetch_report_dates():
    client = get_supabase_client()
    rows = _execute(
        client.table("problems")
        .select("report_date")
        .order("report_date", desc=True)
        .limit(ROW_LIMIT)
    )
    dates = sorted({row.get("report_date") for row in rows if row.get("report_date")}, reverse=True)
    return dates or [date.today().isoformat()]


def fetch_problems(report_date=None, seller_id=None, reason=None):
    client = get_supabase_client()
    query = client.table("problems").select("*").limit(ROW_LIMIT)
    if report_date:
        query = query.eq("report_date", str(report_date))
    if seller_id and seller_id != "Все продавцы":
        query = query.eq("seller_id", seller_id)
    rows = _execute(query)
    if reason and reason != "Все причины":
        rows = [row for row in rows if reason in {row.get("root_cause"), row.get("problem_label"), row.get("problem_type"), row.get("decline_source"), row.get("metric")}]
    return rows


def fetch_data_quality(report_date=None):
    client = get_supabase_client()
    quality = {
        "problems_without_seller_id": 0,
        "ads_bid_history_without_seller_id": 0,
        "sku_without_ads": 0,
        "sku_without_supplies": 0,
    }

    problems_query = client.table("problems").select("seller_id,report_date").limit(ROW_LIMIT)
    if report_date:
        problems_query = problems_query.eq("report_date", str(report_date))
    problems = _execute(problems_query)
    quality["problems_without_seller_id"] = sum(1 for row in problems if not row.get("seller_id"))

    ads_query = client.table("ads_bid_history").select("seller_id,report_date").limit(ROW_LIMIT)
    if report_date:
        ads_query = ads_query.eq("report_date", str(report_date))
    ads_rows = _execute(ads_query)
    quality["ads_bid_history_without_seller_id"] = sum(1 for row in ads_rows if not row.get("seller_id"))

    coverage_query = client.table("api_coverage_daily").select("*").limit(ROW_LIMIT)
    if report_date:
        coverage_query = coverage_query.eq("report_date", str(report_date))
    try:
        coverage = _execute(coverage_query)
    except Exception:
        coverage = []

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
