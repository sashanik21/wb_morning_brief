import os
from datetime import UTC, date, datetime, time, timedelta
from time import perf_counter
from zoneinfo import ZoneInfo

import pandas as pd

from app.analyzers.ads_analyzer import (
    aggregate_ads_rows,
    analyze_ads_problems,
    build_ads_summary,
    enrich_ads_time_series,
    save_ads_report,
)
from app.analyzers.ads_attribution import attribute_ads_rows
from app.analyzers.business_ranking import log_business_ranking, rank_problem_records
from app.analyzers.decision_engine import apply_decision_engine
from app.analyzers.forecast_engine import build_predictive_forecasts
from app.analyzers.perfume_intelligence import (
    build_perfume_intelligence,
    enrich_perfume_records,
)
from app.analyzers.products_enrichment import enrich_funnel_data_with_products
from app.analyzers.qbiki_metrics import build_qbiki_problems, enrich_qbiki_metrics
from app.analyzers.root_cause_analyzer import analyze_root_causes
from app.analyzers.stocks_analyzer import build_stocks_daily_rows
from app.analyzers.tasks_builder import build_tasks_from_problems
from app.collectors.ads import ads_api_had_429, ads_rate_limit_stats, collect_ads_stats
from app.collectors.ads_clusters_collector import (
    ADS_CLUSTER_FORCE_CAMPAIGN_IDS_ENV,
    ADS_CLUSTER_MAX_CAMPAIGNS_PER_SELLER,
    collect_ads_clusters,
)
from app.collectors.funnel import (
    build_top_funnel_drop_signals,
    calculate_funnel_summary_dynamics,
    collect_sales_funnel,
    count_sku_ignored_by_abc_filter,
    flatten_sales_funnel_data,
    save_funnel_problems_report,
    save_sales_funnel_report,
)
from app.collectors.qbiki import collect_qbiki_metrics
from app.collectors.supplies import collect_supply_stock_metrics
from app.config import REQUIRED_SELLERS, set_wb_api_token
from app.reports.api_coverage import (
    build_api_coverage_report,
    coverage_summary_line,
    print_api_coverage_summary,
    save_api_coverage_report,
)
from app.reports.evidence import EVIDENCE_LIMIT_TELEGRAM, build_evidence_rows
from app.reports.telegram_report import (
    send_seller_3d_analytics,
    send_telegram_morning_brief,
)
from app.storage.storage_factory import get_storage


LOG_LEVEL = os.getenv("LOG_LEVEL", "summary").strip().lower()
PROJECT_TIMEZONE = os.getenv("PROJECT_TIMEZONE", "Europe/Moscow")
SCHEDULED_LOCAL_TIME = os.getenv("SCHEDULED_LOCAL_TIME", "09:05")
SCHEDULED_CRON_UTC = os.getenv("SCHEDULED_CRON_UTC", "05 06 * * *")



def _local_timezone():
    try:
        return ZoneInfo(PROJECT_TIMEZONE)
    except Exception:
        _summary_log(f"TIMEZONE WARNING: invalid PROJECT_TIMEZONE={PROJECT_TIMEZONE}, fallback=UTC")
        return UTC


def _format_dt(value):
    return value.strftime("%Y-%m-%d %H:%M:%S %Z")


def _now_times():
    utc_now = datetime.now(UTC)
    local_now = utc_now.astimezone(_local_timezone())
    return utc_now, local_now


def _log_time_checkpoint(label, started_at=None):
    utc_now, local_now = _now_times()
    message = (
        f"{label}: UTC now: {_format_dt(utc_now)} | "
        f"Local now: {_format_dt(local_now)} | "
        f"Calculated send time: {SCHEDULED_LOCAL_TIME} {PROJECT_TIMEZONE}"
    )
    if started_at is not None:
        message += f" | elapsed={perf_counter() - started_at:.1f}s"
    _summary_log(message)
    return utc_now, local_now


def _parse_hh_mm(value):
    try:
        hour, minute = str(value).strip().split(":", 1)
        return int(hour), int(minute)
    except (TypeError, ValueError):
        return None, None


def _parse_cron_hour_minute(cron_expression):
    parts = str(cron_expression or "").split()
    if len(parts) != 5:
        return None, None

    try:
        return int(parts[1]), int(parts[0])
    except ValueError:
        return None, None


def _workflow_start_utc(default_start_utc):
    raw_value = os.getenv("WORKFLOW_STARTED_AT") or os.getenv("GITHUB_RUN_STARTED_AT")
    if not raw_value:
        return default_start_utc

    normalized = raw_value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        _summary_log(f"TIME WARNING: invalid WORKFLOW_STARTED_AT={raw_value}, fallback=app_start")
        return default_start_utc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _expected_run_for_actual(actual_start_utc):
    cron_hour, cron_minute = _parse_cron_hour_minute(SCHEDULED_CRON_UTC)
    if cron_hour is None or cron_minute is None:
        return None

    expected = datetime.combine(
        actual_start_utc.date(),
        time(cron_hour, cron_minute),
        tzinfo=UTC,
    )
    if actual_start_utc < expected - timedelta(hours=12):
        expected -= timedelta(days=1)
    return expected


def _format_minutes(value):
    return f"{value:.1f}"


def _log_schedule_diagnostics(run_started_utc, app_started_utc):
    local_timezone = _local_timezone()
    workflow_start_utc = _workflow_start_utc(run_started_utc)
    workflow_start_local = workflow_start_utc.astimezone(local_timezone)
    expected_run_utc = _expected_run_for_actual(workflow_start_utc)
    cron_hour, cron_minute = _parse_cron_hour_minute(SCHEDULED_CRON_UTC)
    local_hour, local_minute = _parse_hh_mm(SCHEDULED_LOCAL_TIME)
    delay_minutes = (
        (workflow_start_utc - expected_run_utc).total_seconds() / 60
        if expected_run_utc
        else 0
    )

    _summary_log("GITHUB EVENT:")
    _summary_log(f"event_name={os.getenv('GITHUB_EVENT_NAME', '')}")
    _summary_log(f"schedule={os.getenv('GITHUB_EVENT_SCHEDULE', '')}")
    _summary_log(f"workflow_dispatch={str(os.getenv('GITHUB_EVENT_NAME') == 'workflow_dispatch').lower()}")
    _summary_log("SERVER TIME:")
    _summary_log(f"UTC={_format_dt(app_started_utc.astimezone(UTC))}")
    _summary_log(f"Europe/Moscow={_format_dt(app_started_utc.astimezone(ZoneInfo('Europe/Moscow')))}")
    _summary_log("CRON RAW:")
    _summary_log(SCHEDULED_CRON_UTC)
    _summary_log("EXPECTED UTC:")
    _summary_log(f"{cron_hour:02d}:{cron_minute:02d}" if cron_hour is not None else "unknown")
    _summary_log("EXPECTED MSK:")
    _summary_log(f"{local_hour:02d}:{local_minute:02d}" if local_hour is not None else "unknown")
    _summary_log("EXPECTED RUN:")
    _summary_log(f"cron_expression={SCHEDULED_CRON_UTC}")
    _summary_log(f"next_run_time={_format_dt(expected_run_utc) if expected_run_utc else 'unknown'}")
    _summary_log("ACTUAL RUN:")
    _summary_log(f"workflow_start_UTC={_format_dt(workflow_start_utc)}")
    _summary_log(f"workflow_start_Moscow={_format_dt(workflow_start_local)}")
    _summary_log("DELAY:")
    _summary_log(f"minutes_difference={_format_minutes(delay_minutes)}")
    if abs(delay_minutes) > 10:
        _summary_log("WARNING:")
        _summary_log("workflow started later than expected")
        _summary_log(f"delay_minutes={_format_minutes(delay_minutes)}")

    return {
        "scheduled_time": expected_run_utc,
        "actual_start": workflow_start_utc,
        "delay_before_start": delay_minutes,
    }


def _log_scheduler_summary(schedule_diagnostics, telegram_sent_utc, total_duration):
    scheduled_time = schedule_diagnostics.get("scheduled_time")
    actual_start = schedule_diagnostics.get("actual_start")
    delay_before_start = schedule_diagnostics.get("delay_before_start", 0)
    processing_duration_minutes = total_duration / 60
    total_delay = (
        (telegram_sent_utc - scheduled_time).total_seconds() / 60
        if scheduled_time
        else processing_duration_minutes
    )

    _summary_log("SCHEDULER DIAGNOSTICS")
    _summary_log(f"scheduled_time={_format_dt(scheduled_time) if scheduled_time else 'unknown'}")
    _summary_log(f"actual_start={_format_dt(actual_start) if actual_start else 'unknown'}")
    _summary_log(f"telegram_sent_time={_format_dt(telegram_sent_utc)}")
    _summary_log(f"delay_before_start={_format_minutes(delay_before_start)}")
    _summary_log(f"processing_duration={_format_minutes(processing_duration_minutes)}")
    _summary_log(f"total_delay={_format_minutes(total_delay)}")


def _run_timed_stage(label, action):
    stage_started_at = perf_counter()
    _log_time_checkpoint(f"{label} started", stage_started_at)
    try:
        return action()
    finally:
        _log_time_checkpoint(f"{label} finished", stage_started_at)

def _debug_log(*args):
    if LOG_LEVEL == "debug":
        print(*args)


def _summary_log(*args):
    print(*args)


def _extract_funnel_products(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    nested_data = data.get("data")

    if isinstance(nested_data, dict) and isinstance(nested_data.get("products"), list):
        return nested_data["products"]

    if isinstance(data.get("products"), list):
        return data["products"]

    if isinstance(nested_data, list):
        return nested_data

    return []


def _seller_id(seller):
    if not isinstance(seller, dict):
        return None

    return seller.get("seller_id") or seller.get("id")


def _seller_name(seller):
    if not isinstance(seller, dict):
        return ""

    return seller.get("seller_name") or seller.get("name") or ""


def _seller_insert_payload(required_seller, existing_sellers):
    existing_keys = {key for seller in existing_sellers or [] for key in seller.keys()}
    default_keys = {
        "seller_name",
        "name",
        "cabinet_name",
        "status",
        "wb_api_token_env",
        "wb_token_secret_name",
    }
    target_keys = existing_keys & default_keys or default_keys - {"name"}
    payload = {
        key: value
        for key, value in required_seller.items()
        if key in target_keys and value not in (None, "")
    }

    if "name" in target_keys:
        payload["name"] = required_seller.get("name") or required_seller.get("seller_name")
    if "seller_name" in target_keys:
        payload["seller_name"] = required_seller.get("seller_name") or required_seller.get("name")

    payload.setdefault("seller_name", required_seller.get("seller_name") or required_seller.get("name"))
    payload.setdefault("status", "active")
    return payload


def _next_seller_id(storage, existing_sellers):
    numeric_ids = []
    for seller in existing_sellers or []:
        seller_id = _seller_id(seller)
        try:
            numeric_ids.append(int(seller_id))
        except (TypeError, ValueError):
            continue

    if hasattr(storage, "_get_client"):
        try:
            response = (
                storage._get_client()
                .table("sellers")
                .select("id")
                .order("id", desc=True)
                .limit(1)
                .execute()
            )
            for row in response.data or []:
                try:
                    numeric_ids.append(int(row.get("id")))
                except (AttributeError, TypeError, ValueError):
                    continue
        except Exception as error:
            _summary_log(f"SELLER AUTO-CREATE: next id lookup failed error={error}")

    return (max(numeric_ids) if numeric_ids else 0) + 1

def _ensure_required_sellers(storage, sellers):
    ensured_sellers = list(sellers or [])
    existing_names = {_seller_name(seller) for seller in ensured_sellers}

    for required_seller in REQUIRED_SELLERS:
        seller_name = required_seller.get("seller_name") or required_seller.get("name")
        if seller_name in existing_names:
            continue

        payload = _seller_insert_payload(required_seller, ensured_sellers)
        payload.setdefault("id", _next_seller_id(storage, ensured_sellers))
        inserted = []
        if hasattr(storage, "_get_client"):
            try:
                response = storage._get_client().table("sellers").insert(payload).execute()
                inserted = response.data or []
                _summary_log(f"SELLER AUTO-CREATE: {seller_name} status=created")
            except Exception as error:
                _summary_log(f"SELLER AUTO-CREATE: {seller_name} status=failed error={error}")

        if inserted:
            ensured_sellers.extend(inserted)
        else:
            runtime_seller = payload.copy()
            runtime_seller.setdefault("seller_name", seller_name)
            runtime_seller.setdefault("status", "active")
            ensured_sellers.append(runtime_seller)
        existing_names.add(seller_name)

    return ensured_sellers


def _to_float(value):
    if value in (None, ""):
        return 0
    try:
        return float(str(value).replace("%", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0


def _normalize_nm_id(value):
    if value in (None, ""):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return str(value).strip() or None


def _extract_nm_ids(*collections):
    nm_ids = []
    seen = set()
    for collection in collections:
        for item in collection or []:
            if not isinstance(item, dict):
                continue
            product = item.get("product", item)
            if not isinstance(product, dict):
                product = item
            nm_id = _normalize_nm_id(
                product.get("nmId") or product.get("nm_id") or product.get("nm")
            )
            if nm_id is not None and str(nm_id) not in seen:
                seen.add(str(nm_id))
                nm_ids.append(nm_id)
    return nm_ids


def _attach_seller_context(rows, seller, seller_id=None):
    seller_name = seller.get("seller_name", "") if isinstance(seller, dict) else ""
    current_seller_id = seller_id if seller_id is not None else _seller_id(seller)

    for row in rows or []:
        if not isinstance(row, dict):
            continue

        row["sellerName"] = seller_name
        row["seller_name"] = seller_name
        row["seller_id"] = current_seller_id
        row["sellerId"] = current_seller_id

    return rows or []


def _print_problem_owner_check(seller_name, problems):
    owners = sorted(
        {
            str(problem.get("sellerName") or problem.get("seller_name") or "")
            for problem in problems or []
            if isinstance(problem, dict)
        }
    )

    _debug_log("SELLER PROBLEMS OWNER CHECK:")
    _debug_log(f"seller: {seller_name}")
    _debug_log(f"problems: {len(problems or [])}")
    _debug_log(f"unique sellerNames: {', '.join(owners) if owners else ''}")


def _ads_history_row_to_report_row(row, seller_id=None):
    if not isinstance(row, dict):
        return {}

    clicks = _to_float(row.get("clicks"))
    impressions = _to_float(row.get("impressions"))
    spend = _to_float(row.get("spend"))
    orders_sum = _to_float(row.get("ordersSum") or row.get("revenue"))

    return {
        "date": row.get("date") or row.get("report_date") or row.get("reportDate"),
        "selectedPeriod": row.get("date")
        or row.get("report_date")
        or row.get("reportDate"),
        "seller_id": seller_id or row.get("seller_id") or row.get("sellerId"),
        "campaignId": row.get("campaignId") or row.get("campaign_id"),
        "campaignName": row.get("campaignName") or row.get("campaign_name"),
        "campaignStatus": row.get("campaignStatus") or row.get("campaign_status"),
        "campaignType": row.get("campaignType") or row.get("campaign_type"),
        "nmId": row.get("nmId") or row.get("nm_id") or row.get("nm"),
        "vendorCode": row.get("vendorCode") or row.get("vendor_code"),
        "title": row.get("title"),
        "impressions": impressions,
        "clicks": clicks,
        "ctr": (
            row.get("ctr")
            if row.get("ctr") not in (None, "")
            else (clicks / impressions * 100 if impressions else 0)
        ),
        "cpc": (
            row.get("cpc")
            if row.get("cpc") not in (None, "")
            else (spend / clicks if clicks else 0)
        ),
        "spend": spend,
        "orders": row.get("orders")
        or row.get("orders_count")
        or row.get("ordersCount")
        or 0,
        "ordersSum": orders_sum,
        "drr": (
            row.get("drr")
            if row.get("drr") not in (None, "")
            else (spend / orders_sum * 100 if orders_sum else 0)
        ),
        "bid": row.get("bid"),
        "avgPosition": row.get("avgPosition") or row.get("avg_position"),
        "source": "supabase_ads_history",
        "adsSource": "history_supabase",
    }


def _load_ads_history_fallback(storage, seller_id, nm_ids):
    if not (storage and hasattr(storage, "get_latest_ads_metrics_by_nm_ids")):
        return []

    rows = storage.get_latest_ads_metrics_by_nm_ids(seller_id, nm_ids)

    return [
        _ads_history_row_to_report_row(row, seller_id=seller_id) for row in rows or []
    ]


def _sum_report_column(dataframe, column_name):
    if column_name not in dataframe:
        return 0

    numeric_values = pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0)
    total = numeric_values.sum()

    return int(total) if float(total).is_integer() else round(float(total), 2)


def _summary_total(summary_dynamics, summary_key, fallback_report, fallback_column):
    summary_value = summary_dynamics.get(summary_key)

    if summary_value not in (None, ""):
        return summary_value

    return _sum_report_column(fallback_report, fallback_column)


def _build_summary_stats(
    storage_status,
    seller_name,
    total_sku_from_api,
    sku_in_products,
    sku_not_in_products,
    below_abc_threshold_problems,
    critical_problems_count,
    funnel_data,
    supply_stock_metrics_by_nm_id=None,
):
    funnel_report = flatten_sales_funnel_data(funnel_data)
    funnel_summary_dynamics = calculate_funnel_summary_dynamics(funnel_data)

    supply_stock_metrics = supply_stock_metrics_by_nm_id or {}
    supply_totals = {
        "incomingStock": sum(
            _to_float(metric.get("incomingStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "acceptanceStock": sum(
            _to_float(metric.get("acceptanceStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "transitStock": sum(
            _to_float(metric.get("transitStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "readyForSaleStock": sum(
            _to_float(metric.get("readyForSaleStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "matchedSkuCount": len(supply_stock_metrics),
    }

    return {
        "sellerName": seller_name,
        "storage": storage_status,
        "totalSkuFromApi": total_sku_from_api,
        "skuInProducts": sku_in_products,
        "skuNotInProducts": sku_not_in_products,
        "skuAfterProductsFilter": total_sku_from_api,
        "skuRemovedByProductsFilter": 0,
        "belowAbcThresholdProblems": below_abc_threshold_problems,
        "criticalProblemsCount": critical_problems_count,
        "totalOrders": _summary_total(
            funnel_summary_dynamics, "selectedOrderCount", funnel_report, "orderCount"
        ),
        "totalOrderSum": _summary_total(
            funnel_summary_dynamics, "selectedOrderSum", funnel_report, "orderSum"
        ),
        "totalOpenCount": _summary_total(
            funnel_summary_dynamics, "selectedOpenCount", funnel_report, "openCount"
        ),
        "totalCartCount": _summary_total(
            funnel_summary_dynamics, "selectedCartCount", funnel_report, "cartCount"
        ),
        "topDropSignals": build_top_funnel_drop_signals(funnel_data),
        "evidenceRows": build_evidence_rows(funnel_data, limit=EVIDENCE_LIMIT_TELEGRAM),
        "funnelData": funnel_data,
        "supplyStockMetrics": supply_totals,
        **funnel_summary_dynamics,
    }


def _print_summary_stats(summary_stats):
    _debug_log("MORNING BRIEF SUMMARY:")
    _debug_log(f"totalSkuFromApi: {summary_stats.get('totalSkuFromApi')}")
    _debug_log(f"skuInProducts: {summary_stats.get('skuInProducts')}")
    _debug_log(f"skuNotInProducts: {summary_stats.get('skuNotInProducts')}")
    _debug_log(f"belowAbcThresholdProblems: {summary_stats.get('belowAbcThresholdProblems')}")


def _qbiki_source_status():
    return (
        "configured"
        if os.getenv("QBIKI_METRICS_PATH") or os.getenv("QBIKI_GOOGLE_SHEETS_EXPORT")
        else "not configured"
    )


def _merge_ads_bid_history(ads_rows, storage, report_date=None):
    if not (storage and hasattr(storage, "get_latest_ads_bid_history_by_nm_ids")):
        _summary_log("ADS BID: changes=0")
        return ads_rows

    nm_ids = [row.get("nmId") or row.get("nm_id") for row in ads_rows or []]
    unique_dates_count = None

    if hasattr(storage, "get_ads_bid_history_unique_dates_count"):
        unique_dates_count = storage.get_ads_bid_history_unique_dates_count(nm_ids)

    bid_rows = storage.get_latest_ads_bid_history_by_nm_ids(
        nm_ids, report_date=report_date
    )
    bid_history_ready = unique_dates_count is not None and unique_dates_count >= 2

    by_nm = {}
    for row in bid_rows or []:
        nm_id = str(row.get("nm_id") or row.get("nmId") or "")
        if not nm_id:
            continue
        by_nm.setdefault(nm_id, []).append(row)

    changed = 0
    raised = lowered = unchanged = without_history = 0

    for bid_row in bid_rows or []:
        if not bid_history_ready or not bid_row.get("has_previous_bid_history"):
            without_history += 1
            continue

        search_delta = float(bid_row.get("search_bid_delta") or 0)
        recommendations_delta = float(bid_row.get("recommendations_bid_delta") or 0)
        max_delta = max(search_delta, recommendations_delta, key=abs)

        if max_delta > 0:
            raised += 1
        elif max_delta < 0:
            lowered += 1
        else:
            unchanged += 1

    for row in ads_rows or []:
        nm_id = str(row.get("nmId") or row.get("nm_id") or "")
        matches = by_nm.get(nm_id) or []

        if not matches:
            continue

        row["bidChanges"] = matches
        row["adsBidHistoryUniqueDates"] = unique_dates_count
        row["adsBidHistoryReady"] = bid_history_ready
        row["adsBidAnalytics"] = {
            "campaigns_raised": raised,
            "campaigns_lowered": lowered,
            "campaigns_unchanged": unchanged,
            "campaigns_without_history": without_history,
            "campaigns_with_history": raised + lowered + unchanged,
            "unique_dates_count": unique_dates_count,
            "bid_history_ready": bid_history_ready,
        }

        if not bid_history_ready:
            continue

        significant = max(
            matches,
            key=lambda item: max(
                abs(float(item.get("search_bid_delta") or 0)),
                abs(float(item.get("recommendations_bid_delta") or 0)),
            ),
        )

        for source, target in (
            ("campaign_id", "bidCampaignId"),
            ("bid_type", "bidType"),
            ("payment_type", "paymentType"),
            ("search_bid", "searchBid"),
            ("previous_search_bid", "previousSearchBid"),
            ("search_bid_delta", "searchBidDelta"),
            ("recommendations_bid", "recommendationsBid"),
            ("previous_recommendations_bid", "previousRecommendationsBid"),
            ("recommendations_bid_delta", "recommendationsBidDelta"),
        ):
            if significant.get(source) not in (None, ""):
                row[target] = significant.get(source)

        if row.get("searchBidDelta") not in (None, "", 0) or row.get(
            "recommendationsBidDelta"
        ) not in (None, "", 0):
            changed += 1

    _summary_log(
        f"ADS BID: changes={changed} raised={raised} lowered={lowered} "
        f"unchanged={unchanged} without_history={without_history}"
    )

    return ads_rows


def _matched_qbiki_nm_ids(qbiki_rows, funnel_rows, ads_rows):
    known_nm_ids = {
        str(row.get("nmId") or row.get("nm_id"))
        for row in (funnel_rows or []) + (ads_rows or [])
        if isinstance(row, dict)
        and (row.get("nmId") or row.get("nm_id")) not in (None, "")
    }

    return len(
        {
            str(row.get("nmId") or row.get("nm_id"))
            for row in qbiki_rows or []
            if isinstance(row, dict)
            and (row.get("nmId") or row.get("nm_id")) not in (None, "")
            and str(row.get("nmId") or row.get("nm_id")) in known_nm_ids
        }
    )


def _iter_nested_dicts(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_nested_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nested_dicts(item)


def _count_zero_stock_problems(problems):
    return sum(
        1
        for problem in problems or []
        if isinstance(problem, dict)
        and (
            problem.get("problemCategory") == "stocks"
            or problem.get("metric") in ("wbStocks", "realSellableStock", "stocks")
        )
        and _to_float(
            problem.get("currentValue")
            or problem.get("stock")
            or problem.get("wbStocks")
            or problem.get("selectedValue")
        )
        <= 0
        and _has_factual_stock_data(problem)
    )


FACTUAL_STOCK_FIELDS = (
    "realSellableStock",
    "wbStocks",
    "mpStocks",
    "readyForSaleStock",
    "incomingStock",
    "returningStock",
    "acceptanceStock",
    "transitStock",
    "stockState",
)


def _has_factual_stock_data(problem):
    return any(
        problem.get(field) not in (None, "")
        for field in FACTUAL_STOCK_FIELDS
    )


def _is_stock_problem_without_data(problem):
    if not isinstance(problem, dict):
        return False
    return (
        problem.get("problemType") == "sellableOutOfStock"
        or problem.get("metric")
        in {"sellableOutOfStock", "realSellableStock", "wbStocks", "warehouseStockZero", "stocks"}
        or problem.get("problemCategory") == "stocks"
    ) and not _has_factual_stock_data(problem)


def _downgrade_stock_problems_without_data(problems):
    for problem in problems or []:
        if _is_stock_problem_without_data(problem):
            problem["severity"] = "low"
            problem["severityScore"] = min(float(problem.get("severityScore") or 0), 20)
            problem["impactConfidence"] = "LOW"
            problem["recommendation"] = (
                "Недостаточно данных по остаткам: проверить Supplies API / складские данные."
            )
            problem["isBelowAbcThreshold"] = True
    return problems


def _coverage_status(total_rows, total_sku):
    if total_rows:
        return "full"
    if total_sku:
        return "missing"
    return "no_data"


def _build_seller_result(
    seller,
    *,
    processing_status,
    total_sku=0,
    funnel_rows=None,
    ads_rows=None,
    supplies_rows=None,
    problems=None,
    ads_summary=None,
    stocks_summary=None,
    error_message=None,
):
    seller_id = _seller_id(seller)
    seller_name = seller.get("seller_name", "")

    problems = _attach_seller_context(problems or [], seller, seller_id)
    ads_rows = _attach_seller_context(ads_rows or [], seller, seller_id)
    funnel_rows = _attach_seller_context(funnel_rows or [], seller, seller_id)
    supplies_rows = _attach_seller_context(supplies_rows or [], seller, seller_id)

    critical_count = sum(
        1
        for problem in problems
        if isinstance(problem, dict)
        and (
            str(problem.get("severity") or "").lower() == "critical"
            or _to_float(problem.get("severityScore")) >= 70
        )
    )
    warning_count = sum(
        1
        for problem in problems
        if isinstance(problem, dict)
        and not (
            str(problem.get("severity") or "").lower() == "critical"
            or _to_float(problem.get("severityScore")) >= 70
        )
    )

    potential_revenue_loss = sum(
        _to_float(problem.get("potentialRevenueLoss"))
        for problem in problems
        if isinstance(problem, dict)
    )
    lost_order_sum = sum(
        _to_float(problem.get("lostOrderSum"))
        for problem in problems
        if isinstance(problem, dict)
    )
    business_impact_score = max(
        [_to_float(problem.get("businessImpactScore")) for problem in problems]
        or [0]
    )

    ads_red_count = sum(
        1
        for problem in problems
        if isinstance(problem, dict)
        and (
            problem.get("problemCategory") == "ads"
            or str(problem.get("metric") or "").lower() in ("ctr", "cpc", "drr", "cpm")
        )
        and (
            str(problem.get("severity") or "").lower() == "critical"
            or _to_float(problem.get("severityScore")) >= 70
        )
    )
    ads_yellow_count = sum(
        1
        for problem in problems
        if isinstance(problem, dict)
        and (
            problem.get("problemCategory") == "ads"
            or str(problem.get("metric") or "").lower() in ("ctr", "cpc", "drr", "cpm")
        )
        and not (
            str(problem.get("severity") or "").lower() == "critical"
            or _to_float(problem.get("severityScore")) >= 70
        )
    )

    zero_stocks_count = _count_zero_stock_problems(problems)
    oos_risk_count = sum(
        1
        for problem in problems
        if isinstance(problem, dict)
        and (
            problem.get("forecastType") == "OOS"
            or _to_float(problem.get("daysUntilOOS")) > 0
        )
    )

    return {
        "seller_name": seller_name,
        "seller_id": seller_id,
        "status": seller.get("status"),
        "processing_status": processing_status,
        "total_sku": total_sku,
        "funnel_coverage": _coverage_status(len(funnel_rows), total_sku),
        "ads_coverage": _coverage_status(len(ads_rows), total_sku),
        "supplies_coverage": _coverage_status(len(supplies_rows), total_sku),
        "critical_problems_count": critical_count,
        "warning_problems_count": warning_count,
        "ads_red_count": ads_red_count,
        "ads_yellow_count": ads_yellow_count,
        "ads_green_count": max(len(ads_rows) - ads_red_count - ads_yellow_count, 0),
        "zero_stocks_count": zero_stocks_count,
        "oos_zero_count": zero_stocks_count,
        "oos_risk_count": oos_risk_count,
        "potentialRevenueLoss": potential_revenue_loss,
        "lostOrderSum": lost_order_sum,
        "businessImpactScore": business_impact_score,
        "top_problems": problems[:5],
        "problems": problems,
        "ads_summary": ads_summary or {},
        "stocks_summary": stocks_summary or {},
        "error_message": error_message,
        "funnel_rows_count": len(funnel_rows),
        "ads_rows_count": len(ads_rows),
        "supplies_rows_count": len(supplies_rows),
    }


def _print_seller_processing_result(result):
    problems_count = result.get("critical_problems_count", 0) + result.get(
        "warning_problems_count", 0
    )
    _summary_log(
        "SELLER | "
        f"{result.get('seller_name')} | "
        f"status={result.get('processing_status')} | "
        f"sku={result.get('total_sku')} | "
        f"funnel={result.get('funnel_rows_count')} | "
        f"ads={result.get('ads_rows_count')} | "
        f"supplies={result.get('supplies_rows_count')} | "
        f"problems={problems_count} | "
        f"error={result.get('error_message') or ''}"
    )


def _print_multi_seller_processing(active_sellers, seller_results):
    statuses = {"success": 0, "partial": 0, "no_data": 0, "failed": 0}

    for result in seller_results:
        status = result.get("processing_status")
        if status in statuses:
            statuses[status] += 1

    _summary_log(
        "MULTI SELLER | "
        f"active={len(active_sellers)} | "
        f"results={len(seller_results)} | "
        f"success={statuses['success']} | "
        f"partial={statuses['partial']} | "
        f"no_data={statuses['no_data']} | "
        f"failed={statuses['failed']}"
    )

    if LOG_LEVEL == "debug":
        for result in seller_results:
            _print_seller_processing_result(result)

    if len(seller_results) < len(active_sellers):
        _summary_log("WARNING: seller_results count does not match active sellers count")


def _process_seller(storage, seller, report_date):
    seller_name = seller.get("seller_name", "")
    seller_id = _seller_id(seller)
    wb_token_secret_name = seller.get("wb_api_token_env") or seller.get("wb_token_secret_name")
    wb_token = set_wb_api_token(wb_token_secret_name)

    _summary_log(f"SELLER START: {seller_name}")
    _summary_log(f"Seller token configured: {str(bool(wb_token)).lower()}")

    if not wb_token:
        seller_result = _build_seller_result(
            seller,
            processing_status="failed",
            error_message="secret not found",
        )
        _summary_log(f"SELLER FINISH: {seller_name} status=failed error=secret not found")
        return {
            "seller_result": seller_result,
            "summary_stats": {},
            "all_problems": [],
            "root_cause_insights": [],
            "tasks": [],
            "seller_3d_analytics": {},
        }

    products = storage.get_products()
    change_log = storage.get_change_log()
    _debug_log(f"PRODUCTS LOADED: {len(products)}")
    _debug_log(f"CHANGE_LOG LOADED: {len(change_log)}")

    data = _run_timed_stage("DATA COLLECTION funnel", collect_sales_funnel)

    if data is None:
        seller_result = _build_seller_result(
            seller,
            processing_status="no_data",
            error_message="nmIDs not found",
        )
        _summary_log(f"SELLER FINISH: {seller_name} status=no_data error=nmIDs not found")
        return {
            "seller_result": seller_result,
            "summary_stats": {},
            "all_problems": [],
            "root_cause_insights": [],
            "tasks": [],
            "seller_3d_analytics": {},
        }

    wb_cards = _extract_funnel_products(data)
    _attach_seller_context(wb_cards, seller, seller_id)

    if not wb_cards:
        seller_result = _build_seller_result(
            seller,
            processing_status="no_data",
            error_message="nmIDs not found",
        )
        _summary_log(f"SELLER FINISH: {seller_name} status=no_data error=nmIDs not found")
        return {
            "seller_result": seller_result,
            "summary_stats": {},
            "all_problems": [],
            "root_cause_insights": [],
            "tasks": [],
            "seller_3d_analytics": {},
        }

    storage.sync_products_from_wb_cards(seller_id, wb_cards)
    products = storage.get_products()

    total_sku_from_api = len(wb_cards)
    data = enrich_funnel_data_with_products(data, products)
    enriched_products = _extract_funnel_products(data)
    _attach_seller_context(enriched_products, seller, seller_id)

    sku_in_products = sum(
        1
        for funnel_product in enriched_products
        if (
            funnel_product.get("product", funnel_product)
            if isinstance(funnel_product, dict)
            else {}
        ).get("productInCatalog")
        is True
    )
    sku_not_in_products = len(enriched_products) - sku_in_products

    _summary_log(
        f"FUNNEL: seller={seller_name} sku={total_sku_from_api} "
        f"in_catalog={sku_in_products} not_in_catalog={sku_not_in_products}"
    )

    top_drop_nm_ids = [
        signal.get("nmId") or signal.get("nm_id")
        for signal in build_top_funnel_drop_signals(data)
        if isinstance(signal, dict)
    ]
    oos_nm_ids = [
        row.get("nmId") or row.get("nm_id")
        for row in _iter_nested_dicts(data)
        if str(row.get("forecastType") or "").upper() == "OOS"
        or row.get("daysUntilOOS") not in (None, "")
    ]

    ads_data = _run_timed_stage(
        "DATA COLLECTION ads",
        lambda: collect_ads_stats(
            seller_id=seller_id,
            seller_name=seller_name,
            top_drop_nm_ids=top_drop_nm_ids,
            oos_nm_ids=oos_nm_ids,
        ),
    )
    _attach_seller_context(ads_data, seller, seller_id)

    raw_ads_rows_count = len(ads_data or [])
    ads_source = "WB Ads API"
    ads_fallback_used = False

    if raw_ads_rows_count == 0:
        fallback_nm_ids = _extract_nm_ids(wb_cards, products)
        fallback_ads_data = _load_ads_history_fallback(
            storage, seller_id, fallback_nm_ids
        )
        _attach_seller_context(fallback_ads_data, seller, seller_id)

        _summary_log(
            f"ADS FALLBACK: seller={seller_name} source=supabase rows={len(fallback_ads_data)}"
        )

        if fallback_ads_data:
            ads_data = fallback_ads_data
            ads_source = "история Supabase"
            ads_fallback_used = True

    ads_data, ads_matching_debug = attribute_ads_rows(ads_data, wb_cards + products)
    _attach_seller_context(ads_data, seller, seller_id)

    ads_data = aggregate_ads_rows(ads_data)
    _attach_seller_context(ads_data, seller, seller_id)

    aggregated_ads_rows_count = len(ads_data or [])
    advertised_sku_count = len(
        {row.get("nmId") for row in ads_data if row.get("nmId") not in (None, "")}
    )

    ads_data = enrich_ads_time_series(ads_data, storage=storage, seller_id=seller_id)
    _attach_seller_context(ads_data, seller, seller_id)

    _summary_log(
        "ADS CLUSTERS CONFIG: "
        f"seller_id={seller_id} max_campaigns_per_seller={ADS_CLUSTER_MAX_CAMPAIGNS_PER_SELLER} "
        f"force_campaign_ids={os.getenv(ADS_CLUSTER_FORCE_CAMPAIGN_IDS_ENV, '')}"
    )
    _run_timed_stage(
        "DATA COLLECTION ads clusters",
        lambda: collect_ads_clusters(
            report_date=report_date,
            seller_id=seller_id,
            seller_name=seller_name,
        ),
    )

    ads_history_available = any(
        row.get("ads_history_status") in {"avg3", "previous_day"}
        for row in ads_data or []
    )
    ads_current_api_partial = ads_api_had_429() and not (
        raw_ads_rows_count > 0 and ads_history_available
    )
    ads_data = _merge_ads_bid_history(ads_data, storage, report_date=report_date)
    _attach_seller_context(ads_data, seller, seller_id)

    funnel_report = flatten_sales_funnel_data(data)
    funnel_rows = funnel_report.to_dict("records")
    _attach_seller_context(funnel_rows, seller, seller_id)

    ads_problems = analyze_ads_problems(
        ads_data, funnel_report, ads_api_partial=ads_current_api_partial
    )
    _attach_seller_context(ads_problems, seller, seller_id)

    perfume_intelligence = build_perfume_intelligence(funnel_rows, ads_data)
    funnel_rows = perfume_intelligence["rows"]
    _attach_seller_context(funnel_rows, seller, seller_id)

    raw_qbiki_rows = _run_timed_stage("DATA COLLECTION qbiki", collect_qbiki_metrics)
    qbiki_metrics = enrich_qbiki_metrics(
        raw_qbiki_rows, funnel_rows=funnel_rows, ads_rows=ads_data
    )
    _attach_seller_context(qbiki_metrics, seller, seller_id)

    qbiki_source_status = _qbiki_source_status()
    qbiki_matched_nm_ids = _matched_qbiki_nm_ids(qbiki_metrics, funnel_rows, ads_data)

    _summary_log(
        f"QBIKI: seller={seller_name} source={qbiki_source_status} "
        f"rows={len(raw_qbiki_rows)} matched={qbiki_matched_nm_ids}"
    )

    qbiki_problems = build_qbiki_problems(qbiki_metrics)
    _attach_seller_context(qbiki_problems, seller, seller_id)

    if qbiki_metrics and hasattr(storage, "save_daily_qbiki_metrics"):
        storage.save_daily_qbiki_metrics(qbiki_metrics)

    ads_summary = build_ads_summary(ads_data, ads_problems + qbiki_problems)
    ads_summary["rawRows"] = raw_ads_rows_count
    ads_summary["aggregatedRows"] = aggregated_ads_rows_count
    ads_summary["advertisedSku"] = advertised_sku_count
    ads_summary["totalSku"] = total_sku_from_api
    ads_summary["source"] = ads_source
    ads_summary["adsSource"] = ads_source
    ads_summary["fallbackUsed"] = ads_fallback_used

    _summary_log(
        f"ADS: seller={seller_name} campaigns={ads_summary['activeCampaigns']} "
        f"rows={ads_summary['adsRows']} problems={ads_summary['problems']} "
        f"sku={advertised_sku_count}/{total_sku_from_api}"
    )

    stocks_problems = []
    try:
        supply_stock_metrics_by_nm_id = _run_timed_stage(
            "DATA COLLECTION supplies", collect_supply_stock_metrics
        )
    except Exception as error:
        _summary_log(f"SUPPLIES: seller={seller_name} status=failed reason={error}")
        supply_stock_metrics_by_nm_id = {}

    report_path = save_sales_funnel_report(data)
    ads_report_path = save_ads_report(ads_data, ads_problems)

    predictive_forecasts = build_predictive_forecasts(
        funnel_rows, ads_rows=ads_data, storage=storage, seller_id=seller_id
    )

    problems_report_path = save_funnel_problems_report(
        data,
        seller_id=seller_id,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
        ads_rows=ads_data,
        predictive_forecasts=predictive_forecasts,
        report_date=report_date,
    )

    _debug_log(f"XLSX funnel: {report_path}")
    _debug_log(f"XLSX ads: {ads_report_path}")
    _debug_log(f"XLSX problems: {problems_report_path}")
    _debug_log(f"PREDICTIVE FORECASTS: {len(predictive_forecasts)}")

    if hasattr(storage, "save_stocks_daily"):
        stocks_daily_rows, stocks_quality = build_stocks_daily_rows(
            funnel_rows,
            supply_stock_metrics_by_nm_id,
            predictive_forecasts=predictive_forecasts,
            seller=seller,
            seller_id=seller_id,
            report_date=report_date,
        )
        saved_count = storage.save_stocks_daily(stocks_daily_rows)
        stocks_quality["saved_to_stocks_daily"] = saved_count
        print("STOCKS DATA QUALITY:")
        print(f"seller: {seller_name}")
        print(f"sku total: {stocks_quality['sku_total']}")
        print(f"stock metrics loaded: {stocks_quality['stock_metrics_loaded']}")
        print(f"saved to stocks_daily: {stocks_quality['saved_to_stocks_daily']}")
        print(f"confirmed oos: {stocks_quality['confirmed_oos']}")
        print(f"oos risk: {stocks_quality['oos_risk']}")
        print(f"no stock data: {stocks_quality['no_stock_data']}")
        print(f"incoming/transit: {stocks_quality['incoming_transit']}")
        if stocks_quality["stock_metrics_loaded"] < stocks_quality["sku_total"]:
            print("STOCKS DATA QUALITY WARNING:")
            print("partial stock coverage")

    funnel_problems_df = pd.read_excel(
        problems_report_path, sheet_name="problems"
    ).fillna("")
    funnel_problems = funnel_problems_df.to_dict("records")
    _attach_seller_context(funnel_problems, seller, seller_id)

    all_problems = enrich_perfume_records(
        funnel_problems + ads_problems + qbiki_problems + stocks_problems
    )
    _attach_seller_context(all_problems, seller, seller_id)
    all_problems = _downgrade_stock_problems_without_data(all_problems)

    if ads_current_api_partial:
        for problem in all_problems:
            if problem.get("problemCategory") == "ads":
                problem["adsConfidence"] = "LOW"
                problem["impactConfidence"] = "LOW"
                problem["severity"] = "low"
                problem["severityScore"] = min(
                    float(problem.get("severityScore") or 0), 20
                )

    all_problems = apply_decision_engine(all_problems)
    _attach_seller_context(all_problems, seller, seller_id)

    all_problems = rank_problem_records(all_problems)
    _attach_seller_context(all_problems, seller, seller_id)

    if LOG_LEVEL == "debug":
        log_business_ranking(all_problems, source="main")

    if funnel_rows:
        storage.save_funnel_snapshot(funnel_rows)

    if all_problems:
        storage.save_problems(all_problems)

    root_cause_insights = analyze_root_causes(all_problems, data)
    _attach_seller_context(root_cause_insights, seller, seller_id)

    summary_stats = _build_summary_stats(
        storage_status=storage.get_storage_status(),
        seller_name=seller_name,
        total_sku_from_api=total_sku_from_api,
        sku_in_products=sku_in_products,
        sku_not_in_products=sku_not_in_products,
        below_abc_threshold_problems=count_sku_ignored_by_abc_filter(data),
        critical_problems_count=len(all_problems),
        funnel_data=data,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
    )

    summary_stats["adsSummary"] = ads_summary
    summary_stats["adsRows"] = ads_data
    summary_stats["adsRawRowsCount"] = raw_ads_rows_count
    summary_stats["adsAggregatedRowsCount"] = aggregated_ads_rows_count
    summary_stats["advertisedSkuCount"] = advertised_sku_count
    summary_stats["adsApiHad429"] = ads_api_had_429()
    summary_stats["adsApiPartial"] = ads_current_api_partial
    summary_stats["adsSource"] = ads_source
    summary_stats["adsFallbackUsed"] = ads_fallback_used
    summary_stats["adsRateLimit"] = ads_rate_limit_stats()
    summary_stats["adsCoverageConfidence"] = ads_rate_limit_stats().get(
        "adsCoverageConfidence"
    )

    if isinstance(ads_summary, dict):
        ads_summary["adsCoverageConfidence"] = summary_stats["adsCoverageConfidence"]

    baseline_counts = {}
    for problem in all_problems:
        baseline_type = problem.get("baselineType") or problem.get("baseline_type")
        if baseline_type:
            baseline_counts[baseline_type] = baseline_counts.get(baseline_type, 0) + 1

    summary_stats["baselineTypeCounts"] = baseline_counts

    if baseline_counts:
        summary_stats["baselineMode"] = max(
            baseline_counts, key=lambda key: baseline_counts.get(key) or 0
        )

    summary_stats["qbikiMetrics"] = qbiki_metrics
    summary_stats["qbikiSourceStatus"] = qbiki_source_status
    summary_stats["qbikiRowsLoaded"] = len(raw_qbiki_rows)
    summary_stats["qbikiMatchedNmIds"] = qbiki_matched_nm_ids

    api_coverage_report = build_api_coverage_report(
        seller_name=seller_name,
        cards=wb_cards,
        products=products,
        funnel_rows=funnel_rows,
        ads_rows=ads_data,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
        problems=all_problems,
        ads_api_partial=ads_current_api_partial,
        qbiki_source_status=qbiki_source_status,
        ads_matching_debug=ads_matching_debug,
    )

    print_api_coverage_summary(api_coverage_report)
    api_coverage_path = save_api_coverage_report(api_coverage_report)
    _debug_log(f"XLSX API coverage: {api_coverage_path}")

    if hasattr(storage, "save_api_coverage_daily"):
        storage.save_api_coverage_daily(
            api_coverage_report["coverage"].to_dict("records")
        )

    summary_stats["apiCoverage"] = {
        "line": coverage_summary_line(api_coverage_report),
        "adsApiHad429": ads_api_had_429(),
        "adsFound": api_coverage_report.get("adsUniqueNmids", 0),
        "adsCampaignCount": api_coverage_report.get("adsCampaignCount", 0),
        "adsRowsCount": api_coverage_report.get("adsRowsCount", 0),
        "totalSku": len(api_coverage_report["coverage"]),
    }
    summary_stats["perfumeIntelligence"] = perfume_intelligence

    current_processing_status = "success" if total_sku_from_api else "no_data"
    current_error_message = None if total_sku_from_api else "nmIDs not found"

    seller_result = _build_seller_result(
        seller,
        processing_status=current_processing_status,
        total_sku=total_sku_from_api,
        funnel_rows=funnel_rows,
        ads_rows=ads_data,
        supplies_rows=list((supply_stock_metrics_by_nm_id or {}).values()),
        problems=all_problems,
        ads_summary=ads_summary,
        stocks_summary={
            "rows": len(supply_stock_metrics_by_nm_id or {}),
            "zeroStocks": _count_zero_stock_problems(all_problems),
        },
        error_message=current_error_message,
    )

    _print_problem_owner_check(seller_name, all_problems)

    tasks = build_tasks_from_problems(all_problems)
    _attach_seller_context(tasks, seller, seller_id)

    seller_3d_analytics = _build_seller_3d_analytics(
        storage,
        seller_result,
        funnel_rows,
        all_problems,
        report_date,
    )

    _summary_log(
        f"SELLER FINISH: {seller_name} status={seller_result.get('processing_status')} "
        f"sku={total_sku_from_api} problems={len(all_problems)} tasks={len(tasks)}"
    )
    _debug_log(
        f"SELLER FINISH DETAILS: {seller_name} "
        f"funnel={len(funnel_rows)} ads={len(ads_data)} "
        f"supplies={len(supply_stock_metrics_by_nm_id or {})} "
        f"root_causes={len(root_cause_insights)}"
    )

    return {
        "seller_result": seller_result,
        "summary_stats": summary_stats,
        "all_problems": all_problems,
        "root_cause_insights": root_cause_insights,
        "tasks": tasks,
        "seller_3d_analytics": seller_3d_analytics,
    }


def _is_business_critical_seller(result):
    if not isinstance(result, dict):
        return False

    if result.get("processing_status") != "success":
        return False

    return any(
        _to_float(result.get(field)) > 0
        for field in (
            "critical_problems_count",
            "ads_red_count",
            "oos_zero_count",
            "oos_risk_count",
            "zero_stocks_count",
        )
    )


def _seller_detail_sort_key(result):
    return (
        _to_float(result.get("critical_problems_count")),
        _to_float(result.get("potentialRevenueLoss")),
        _to_float(result.get("lostOrderSum")),
        _to_float(result.get("ads_red_count")),
        _to_float(result.get("oos_zero_count")),
        _to_float(result.get("oos_risk_count")),
    )


def _filter_by_seller_name(records, seller_name):
    return [
        record
        for record in records or []
        if isinstance(record, dict)
        and (record.get("sellerName") or record.get("seller_name")) == seller_name
    ]


def _parse_report_day(value):
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _row_metric(row, *keys):
    for key in keys:
        if isinstance(row, dict) and row.get(key) not in (None, ""):
            return _to_float(row.get(key))
    return 0


def _seller_period_totals(rows, period_days):
    period_days = set(period_days or [])
    totals = {
        "orders": 0,
        "revenue": 0,
        "opens": 0,
        "carts": 0,
        "days": set(),
    }

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        row_day = _parse_report_day(
            row.get("date") or row.get("report_date") or row.get("reportDate")
        )
        if row_day not in period_days:
            continue

        totals["days"].add(row_day)
        totals["orders"] += _row_metric(row, "orderCount", "order_count")
        totals["revenue"] += _row_metric(row, "orderSum", "order_sum")
        totals["opens"] += _row_metric(row, "openCount", "open_count")
        totals["carts"] += _row_metric(row, "cartCount", "cart_count")

    totals["conversion"] = (
        totals["orders"] / totals["carts"] * 100 if totals["carts"] else 0
    )
    totals["days_count"] = len(totals["days"])
    totals.pop("days", None)
    return totals


def _seller_critical_sku_count(problems):
    return len(
        {
            problem.get("nmId") or problem.get("nm_id")
            for problem in problems or []
            if isinstance(problem, dict)
            and (problem.get("nmId") or problem.get("nm_id")) not in (None, "")
            and (
                str(problem.get("severity") or "").lower() == "critical"
                or _to_float(problem.get("severityScore")) >= 70
            )
        }
    )


def _problem_reason(problem):
    text = " ".join(
        str(problem.get(key) or "")
        for key in ("problemCategory", "problemType", "metric", "rootCause", "diagnosis", "recommendation")
        if isinstance(problem, dict)
    ).lower()
    if "ads" in text or "реклам" in text or any(item in text for item in ("ctr", "cpc", "drr")):
        if problem.get("adsConfidence") == "LOW" or problem.get("impactConfidence") == "LOW":
            return "реклама требует проверки"
        return "реклама"
    if "price" in text or "цен" in text:
        return "цена"
    if "conversion" in text or "конверс" in text or "carttoorder" in text or "addtocart" in text:
        return "конверсия"
    if "stock" in text or "остат" in text or "oos" in text:
        if _is_confirmed_oos_problem(problem):
            return "остатки"
        return "требует проверки"
    return "неизвестно"


def _is_confirmed_oos_problem(problem):
    if not isinstance(problem, dict):
        return False
    stock_state = str(problem.get("stockState") or "").upper()
    stock_value = problem.get("realSellableStock")
    if stock_value in (None, ""):
        stock_value = problem.get("selectedValue")
    has_stock_data = any(problem.get(field) not in (None, "") for field in FACTUAL_STOCK_FIELDS)
    return has_stock_data and _to_float(stock_value) == 0 and stock_state == "BLOCKED"


def _seller_main_reason(problems):
    counts = {}
    for problem in problems or []:
        reason = _problem_reason(problem)
        counts[reason] = counts.get(reason, 0) + 1

    known_counts = {key: value for key, value in counts.items() if key != "неизвестно"}
    if not known_counts:
        return "требует проверки"

    best_reason = max(known_counts, key=known_counts.get)
    if list(known_counts.values()).count(known_counts[best_reason]) > 1:
        return "требует проверки"
    return best_reason


def _build_seller_3d_analytics(storage, seller_result, funnel_rows, problems, report_date):
    seller_id = seller_result.get("seller_id")
    report_day = _parse_report_day(report_date)
    if not report_day:
        return {}

    current_days = [report_day - timedelta(days=offset) for offset in (2, 1, 0)]
    previous_days = [report_day - timedelta(days=offset) for offset in (5, 4, 3)]

    history_rows = []
    if seller_id not in (None, "") and hasattr(storage, "get_funnel_history"):
        nm_ids = {
            row.get("nmId") or row.get("nm_id")
            for row in funnel_rows or []
            if isinstance(row, dict) and (row.get("nmId") or row.get("nm_id")) not in (None, "")
        }
        for nm_id in nm_ids:
            history_rows.extend(
                storage.get_funnel_history(
                    seller_id,
                    nm_id,
                    8,
                    before_date=report_day - timedelta(days=2),
                )
                or []
            )

    all_rows = list(funnel_rows or []) + history_rows
    current = _seller_period_totals(all_rows, current_days)
    previous = _seller_period_totals(all_rows, previous_days)
    has_previous = previous["days_count"] >= 3

    lost_orders = max(previous["orders"] - current["orders"], 0) if has_previous else 0
    average_check = (
        current["revenue"] / current["orders"]
        if current["orders"]
        else (previous["revenue"] / previous["orders"] if previous["orders"] else 0)
    )
    lost_revenue = lost_orders * average_check if lost_orders > 0 else 0

    critical_sku = _seller_critical_sku_count(problems)
    orders_dynamic = (
        (current["orders"] - previous["orders"]) / previous["orders"] * 100
        if has_previous and previous["orders"]
        else None
    )
    revenue_dynamic = (
        (current["revenue"] - previous["revenue"]) / previous["revenue"] * 100
        if has_previous and previous["revenue"]
        else None
    )
    conversion_dynamic = (
        (current["conversion"] - previous["conversion"]) / previous["conversion"] * 100
        if has_previous and previous["conversion"]
        else None
    )

    status = "insufficient_data"
    if has_previous:
        if lost_revenue > 0 or lost_orders > 0 or (revenue_dynamic is not None and revenue_dynamic < 0):
            status = "drop"
        elif (
            (conversion_dynamic is not None and conversion_dynamic < 0)
            or _to_float(seller_result.get("ads_yellow_count")) > 0
            or critical_sku >= 3
        ):
            status = "attention"
        else:
            status = "stable"

    return {
        "seller_name": seller_result.get("seller_name"),
        "current": current,
        "previous": previous,
        "has_previous": has_previous,
        "orders_dynamic": orders_dynamic,
        "revenue_dynamic": revenue_dynamic,
        "conversion_dynamic": conversion_dynamic,
        "lost_orders": lost_orders,
        "lost_revenue": lost_revenue,
        "critical_sku": critical_sku,
        "main_reason": _seller_main_reason(problems),
        "status": status,
    }


def _send_critical_seller_details(
    seller_results,
    seller_summary_stats_by_name,
    combined_problems,
    combined_root_cause_insights,
):
    critical_sellers = [
        result for result in seller_results if _is_business_critical_seller(result)
    ]

    selected_sellers = sorted(
        critical_sellers,
        key=_seller_detail_sort_key,
        reverse=True,
    )[:3]

    _summary_log(
        "TELEGRAM DETAILS | "
        f"critical={len(critical_sellers)} | "
        f"selected={len(selected_sellers)} | "
        f"sellers={', '.join(result.get('seller_name', '') for result in selected_sellers)}"
    )

    for result in selected_sellers:
        seller_name = result.get("seller_name", "")

        seller_problems = _filter_by_seller_name(combined_problems, seller_name)
        seller_root_causes = _filter_by_seller_name(
            combined_root_cause_insights,
            seller_name,
        )

        seller_summary_stats = dict(seller_summary_stats_by_name.get(seller_name) or {})
        seller_summary_stats["sellerName"] = seller_name
        seller_summary_stats["sellerResults"] = [result]
        seller_summary_stats["activeSellersCount"] = 1
        seller_summary_stats["sellersTotal"] = 1
        seller_summary_stats["sellerNames"] = [seller_name]

        _debug_log(
            f"TELEGRAM DETAIL: {seller_name} "
            f"problems={len(seller_problems)} root_causes={len(seller_root_causes)}"
        )

        send_telegram_morning_brief(
            seller_problems,
            summary_stats=seller_summary_stats,
            root_cause_insights=seller_root_causes,
        )


def main():
    run_started_at = perf_counter()
    run_started_utc, run_started_local = _now_times()
    _summary_log("WB MORNING BRIEF START")
    _summary_log(f"SCHEDULED TIME: {SCHEDULED_LOCAL_TIME} {PROJECT_TIMEZONE} (cron UTC: {SCHEDULED_CRON_UTC})")
    _summary_log(f"ACTUAL START: UTC {_format_dt(run_started_utc)} | Local {_format_dt(run_started_local)}")
    schedule_diagnostics = _log_schedule_diagnostics(run_started_utc, run_started_utc)
    _log_time_checkpoint("Workflow start observed by app", run_started_at)

    storage = get_storage()

    sellers = _ensure_required_sellers(storage, storage.get_sellers())
    active_sellers = [seller for seller in sellers if seller.get("status") == "active"]

    _summary_log(
        f"RUN CONFIG: sellers_loaded={len(sellers)} active_sellers={len(active_sellers)} "
        f"log_level={LOG_LEVEL}"
    )

    report_date = date.today() - timedelta(days=1)

    seller_results = []
    seller_summary_stats_by_name = {}
    combined_problems = []
    combined_root_cause_insights = []
    combined_tasks = []
    seller_3d_analytics = []
    summary_stats = {}

    for seller in active_sellers:
        processed = _process_seller(storage, seller, report_date)

        seller_result = processed["seller_result"]
        seller_name = seller_result.get("seller_name", "")

        seller_results.append(seller_result)
        combined_problems.extend(processed.get("all_problems") or [])
        combined_root_cause_insights.extend(processed.get("root_cause_insights") or [])
        combined_tasks.extend(processed.get("tasks") or [])
        if processed.get("seller_3d_analytics"):
            seller_3d_analytics.append(processed["seller_3d_analytics"])

        if processed.get("summary_stats"):
            seller_summary_stats_by_name[seller_name] = processed["summary_stats"]
            summary_stats = processed["summary_stats"]

    _log_time_checkpoint("REPORT FORMATION started", run_started_at)
    _print_multi_seller_processing(active_sellers, seller_results)

    summary_stats.setdefault("sellerName", "")
    summary_stats["sellerResults"] = seller_results
    summary_stats["activeSellersCount"] = len(active_sellers)
    summary_stats["sellersTotal"] = len(active_sellers)
    summary_stats["sellerNames"] = [
        seller.get("seller_name", "") for seller in active_sellers
    ]

    if seller_results:
        summary_stats["totalSkuFromApi"] = sum(
            _to_float(result.get("total_sku")) for result in seller_results
        )

    if summary_stats:
        _print_summary_stats(summary_stats)

    _summary_log(f"TOTAL PROBLEMS: {len(combined_problems)}")
    telegram_messages_sent = 0
    telegram_started_at = perf_counter()
    telegram_started_utc, telegram_started_local = _log_time_checkpoint("Telegram send started", run_started_at)
    _summary_log("TELEGRAM SUMMARY: sending")

    sent_summary_messages = send_telegram_morning_brief(
        combined_problems,
        summary_stats=summary_stats,
        root_cause_insights=combined_root_cause_insights,
    )
    telegram_messages_sent += int(sent_summary_messages or 0)

    _summary_log("TELEGRAM SUMMARY: sent")

    _summary_log("TELEGRAM SELLER 3D ANALYTICS: sending")
    if send_seller_3d_analytics(seller_3d_analytics, report_date=report_date):
        telegram_messages_sent += 1
    _summary_log("TELEGRAM SELLER 3D ANALYTICS: sent")

    if len(active_sellers) > 1:
        _summary_log("TELEGRAM DETAILS: skipped for morning brief")

    telegram_sent_utc, telegram_sent_local = _log_time_checkpoint("Telegram send finished", telegram_started_at)
    _summary_log(f"TELEGRAM SENT: UTC {_format_dt(telegram_sent_utc)} | Local {_format_dt(telegram_sent_local)}")
    _summary_log(f"TELEGRAM MESSAGES SENT: {telegram_messages_sent}")

    storage.create_tasks(combined_tasks)

    _summary_log(
        "RUN SUMMARY | "
        f"sellers={len(active_sellers)} | "
        f"success={sum(1 for result in seller_results if result.get('processing_status') == 'success')} | "
        f"problems={len(combined_problems)} | "
        f"tasks={len(combined_tasks)}"
    )
    total_duration = perf_counter() - run_started_at
    finish_utc, finish_local = _now_times()
    _summary_log(f"TOTAL DURATION: {total_duration:.1f}s")
    _log_scheduler_summary(schedule_diagnostics, telegram_sent_utc, total_duration)
    _summary_log(
        "SCHEDULE SUMMARY | "
        f"SCHEDULED TIME: {SCHEDULED_LOCAL_TIME} {PROJECT_TIMEZONE} (cron UTC: {SCHEDULED_CRON_UTC}) | "
        f"ACTUAL START: UTC {_format_dt(run_started_utc)} / Local {_format_dt(run_started_local)} | "
        f"TELEGRAM SENT: UTC {_format_dt(telegram_sent_utc)} / Local {_format_dt(telegram_sent_local)} | "
        f"FINISHED: UTC {_format_dt(finish_utc)} / Local {_format_dt(finish_local)} | "
        f"TOTAL DURATION: {total_duration:.1f}s"
    )
    _summary_log("WB MORNING BRIEF FINISHED")


if __name__ == "__main__":
    main()
