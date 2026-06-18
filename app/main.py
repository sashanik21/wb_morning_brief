import os
from datetime import date, timedelta

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
from app.analyzers.tasks_builder import build_tasks_from_problems
from app.collectors.ads import ads_api_had_429, ads_rate_limit_stats, collect_ads_stats
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
from app.config import set_wb_api_token
from app.reports.api_coverage import (
    build_api_coverage_report,
    coverage_summary_line,
    print_api_coverage_summary,
    save_api_coverage_report,
)
from app.reports.evidence import EVIDENCE_LIMIT_TELEGRAM, build_evidence_rows
from app.reports.telegram_report import send_telegram_morning_brief
from app.storage.storage_factory import get_storage


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

    return rows or []


def _print_problem_owner_check(seller_name, problems):
    owners = sorted(
        {
            str(problem.get("sellerName") or problem.get("seller_name") or "")
            for problem in problems or []
            if isinstance(problem, dict)
        }
    )
    print("SELLER PROBLEMS OWNER CHECK:")
    print(f"seller: {seller_name}")
    print(f"problems: {len(problems or [])}")
    print(f"unique sellerNames: {', '.join(owners) if owners else ''}")


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
    print("MORNING BRIEF SUMMARY:")
    print(f"totalSkuFromApi: {summary_stats.get('totalSkuFromApi')}")
    print(f"skuInProducts: {summary_stats.get('skuInProducts')}")
    print(f"skuNotInProducts: {summary_stats.get('skuNotInProducts')}")
    print(f"belowAbcThresholdProblems: {summary_stats.get('belowAbcThresholdProblems')}")


def _qbiki_source_status():
    return (
        "configured"
        if os.getenv("QBIKI_METRICS_PATH") or os.getenv("QBIKI_GOOGLE_SHEETS_EXPORT")
        else "not configured"
    )


def _merge_ads_bid_history(ads_rows, storage, report_date=None):
    if not (storage and hasattr(storage, "get_latest_ads_bid_history_by_nm_ids")):
        print("ads bid changes found: 0")
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

    print(f"ads bid changes found: {changed}")
    print("ADS BID ANALYTICS:")
    print(f"campaigns with history: {raised + lowered + unchanged}")
    print(f"raised: {raised}")
    print(f"lowered: {lowered}")
    print(f"unchanged: {unchanged}")
    print(f"without history: {without_history}")

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
    )


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
    print("SELLER PROCESSING RESULT:")
    print(f"seller: {result.get('seller_name')}")
    print(f"status: {result.get('processing_status')}")
    print(f"total_sku: {result.get('total_sku')}")
    print(f"funnel rows: {result.get('funnel_rows_count')}")
    print(f"ads rows: {result.get('ads_rows_count')}")
    print(f"supplies rows: {result.get('supplies_rows_count')}")
    print(
        "problems: "
        f"{result.get('critical_problems_count', 0) + result.get('warning_problems_count', 0)}"
    )
    print(f"error: {result.get('error_message') or ''}")


def _print_multi_seller_processing(active_sellers, seller_results):
    statuses = {"success": 0, "partial": 0, "no_data": 0, "failed": 0}

    for result in seller_results:
        status = result.get("processing_status")
        if status in statuses:
            statuses[status] += 1

    print("MULTI SELLER PROCESSING:")
    print(f"active sellers: {len(active_sellers)}")
    print(f"seller results created: {len(seller_results)}")

    for status in ("success", "partial", "no_data", "failed"):
        print(f"{status}: {statuses[status]}")

    for result in seller_results:
        _print_seller_processing_result(result)

    if len(seller_results) < len(active_sellers):
        print("WARNING: seller_results count does not match active sellers count")


def _process_seller(storage, seller, report_date):
    seller_name = seller.get("seller_name", "")
    seller_id = _seller_id(seller)
    wb_token_secret_name = seller.get("wb_token_secret_name")
    wb_token = set_wb_api_token(wb_token_secret_name)

    print(f"Текущий продавец: {seller_name}")
    print("SELLER TOKEN:")
    print(f"seller: {seller_name}")
    print(f"secret: {wb_token_secret_name or ''}")
    print(f"token found: {str(bool(wb_token)).lower()}")

    if not wb_token:
        seller_result = _build_seller_result(
            seller,
            processing_status="failed",
            error_message="secret not found",
        )
        print(f"SELLER RESULT CREATED: {seller_result.get('seller_name')}")
        return {
            "seller_result": seller_result,
            "summary_stats": {},
            "all_problems": [],
            "root_cause_insights": [],
            "tasks": [],
        }

    products = storage.get_products()
    print(f"PRODUCTS LOADED: {len(products)}")

    change_log = storage.get_change_log()
    print(f"CHANGE_LOG LOADED: {len(change_log)}")

    data = collect_sales_funnel()

    if data is None:
        print("Данные funnel не получены")
        seller_result = _build_seller_result(
            seller,
            processing_status="no_data",
            error_message="nmIDs not found",
        )
        print(f"SELLER RESULT CREATED: {seller_result.get('seller_name')}")
        return {
            "seller_result": seller_result,
            "summary_stats": {},
            "all_problems": [],
            "root_cause_insights": [],
            "tasks": [],
        }

    print("FUNNEL ДАННЫЕ ПОЛУЧЕНЫ")
    print("=" * 50)

    wb_cards = _extract_funnel_products(data)
    _attach_seller_context(wb_cards, seller, seller_id)

    if not wb_cards:
        seller_result = _build_seller_result(
            seller,
            processing_status="no_data",
            error_message="nmIDs not found",
        )
        print(f"SELLER RESULT CREATED: {seller_result.get('seller_name')}")
        return {
            "seller_result": seller_result,
            "summary_stats": {},
            "all_problems": [],
            "root_cause_insights": [],
            "tasks": [],
        }

    storage.sync_products_from_wb_cards(seller_id, wb_cards)
    products = storage.get_products()
    print(f"PRODUCTS LOADED: {len(products)}")

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
    print("=" * 50)

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

    ads_data = collect_ads_stats(
        seller_id=seller_id, top_drop_nm_ids=top_drop_nm_ids, oos_nm_ids=oos_nm_ids
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

        print("ADS FALLBACK ACTIVATED")
        print("ADS FALLBACK SOURCE: SUPABASE")
        print(f"ADS FALLBACK ROWS: {len(fallback_ads_data)}")

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

    raw_qbiki_rows = collect_qbiki_metrics()
    qbiki_metrics = enrich_qbiki_metrics(
        raw_qbiki_rows, funnel_rows=funnel_rows, ads_rows=ads_data
    )
    _attach_seller_context(qbiki_metrics, seller, seller_id)

    qbiki_source_status = _qbiki_source_status()
    qbiki_matched_nm_ids = _matched_qbiki_nm_ids(qbiki_metrics, funnel_rows, ads_data)

    print("QBIKI DATA:")
    print(f"source: {qbiki_source_status}")
    print(f"rows loaded: {len(raw_qbiki_rows)}")
    print(f"matched nmIds: {qbiki_matched_nm_ids}")

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

    print(f"ADS ДАННЫЕ ПОЛУЧЕНЫ: {len(ads_data)} строк")
    print("ADS SUMMARY:")
    print(f"campaigns: {ads_summary['activeCampaigns']}")
    print(f"ads rows: {ads_summary['adsRows']}")
    print(f"problems: {ads_summary['problems']}")
    print(
        "period: "
        f"{ads_summary.get('selectedPeriod') or 'n/a'} vs "
        f"{ads_summary.get('pastPeriod') or 'n/a'}"
    )
    print("=" * 50)

    stocks_problems = []
    try:
        supply_stock_metrics_by_nm_id = collect_supply_stock_metrics()
    except Exception as error:
        print(f"SUPPLIES COLLECTOR WARNING: {error}")
        print("SUPPLIES DATA: 0 rows")
        print("SUPPLIES API:")
        print("status: disabled_or_failed")
        print(f"reason: {error}")
        supply_stock_metrics_by_nm_id = {}

    report_path = save_sales_funnel_report(data)
    print(f"XLSX отчёт: {report_path}")
    print("=" * 50)

    predictive_forecasts = build_predictive_forecasts(
        funnel_rows, ads_rows=ads_data, storage=storage, seller_id=seller_id
    )
    print(f"PREDICTIVE FORECASTS: {len(predictive_forecasts)}")

    ads_report_path = save_ads_report(ads_data, ads_problems)
    print(f"XLSX отчёт по рекламе: {ads_report_path}")
    print("=" * 50)

    problems_report_path = save_funnel_problems_report(
        data,
        seller_id=seller_id,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
        ads_rows=ads_data,
        predictive_forecasts=predictive_forecasts,
    )
    print(f"XLSX отчёт по проблемам: {problems_report_path}")
    print("=" * 50)

    funnel_problems_df = pd.read_excel(
        problems_report_path, sheet_name="problems"
    ).fillna("")
    funnel_problems = funnel_problems_df.to_dict("records")
    _attach_seller_context(funnel_problems, seller, seller_id)

    all_problems = enrich_perfume_records(
        funnel_problems + ads_problems + qbiki_problems + stocks_problems
    )
    _attach_seller_context(all_problems, seller, seller_id)

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

    log_business_ranking(all_problems, source="main")

    if funnel_rows:
        storage.save_funnel_snapshot(funnel_rows)

    if all_problems:
        storage.save_problems(all_problems)

    root_cause_insights = analyze_root_causes(all_problems, data)
    _attach_seller_context(root_cause_insights, seller, seller_id)

    print(f"ROOT CAUSE INSIGHTS: {len(root_cause_insights)}")

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
    print(f"XLSX отчёт покрытия API: {api_coverage_path}")

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
    print(f"SELLER RESULT CREATED: {seller_result.get('seller_name')}")

    tasks = build_tasks_from_problems(all_problems)
    _attach_seller_context(tasks, seller, seller_id)

    return {
        "seller_result": seller_result,
        "summary_stats": summary_stats,
        "all_problems": all_problems,
        "root_cause_insights": root_cause_insights,
        "tasks": tasks,
    }


def main():
    print("MAIN VERSION: TELEGRAM ENABLED")
    print("=" * 50)
    print("WB MORNING BRIEF")
    print("=" * 50)

    storage = get_storage()
    print("=" * 50)

    sellers = storage.get_sellers()
    print(f"SELLERS LOADED: {len(sellers)}")

    active_sellers = [seller for seller in sellers if seller.get("status") == "active"]
    print(f"Активных продавцов: {len(active_sellers)}")

    report_date = date.today() - timedelta(days=1)

    seller_results = []
    combined_problems = []
    combined_root_cause_insights = []
    combined_tasks = []
    summary_stats = {}

    for seller in active_sellers:
        processed = _process_seller(storage, seller, report_date)
        seller_results.append(processed["seller_result"])
        combined_problems.extend(processed.get("all_problems") or [])
        combined_root_cause_insights.extend(processed.get("root_cause_insights") or [])
        combined_tasks.extend(processed.get("tasks") or [])

        if processed.get("summary_stats"):
            summary_stats = processed["summary_stats"]

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
        summary_stats["adsRows"] = [
            row
            for result in seller_results
            for row in result.get("ads_summary", {}).get("rows", [])
            if isinstance(row, dict)
        ]

    if summary_stats:
        _print_summary_stats(summary_stats)

    print("=" * 50)

    print("TOTAL PROBLEMS:")
    print(f"all: {len(combined_problems)}")
    print("=" * 50)

    print("ОТПРАВЛЯЕМ TELEGRAM MORNING BRIEF")
    send_telegram_morning_brief(
        combined_problems,
        summary_stats=summary_stats,
        root_cause_insights=combined_root_cause_insights,
    )
    print("=" * 50)

    storage.create_tasks(combined_tasks)
    print("=" * 50)

    print("WB Morning Brief completed successfully")


if __name__ == "__main__":
    main()
