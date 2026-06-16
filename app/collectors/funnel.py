from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.analyzers.business_impact import enrich_business_impact_scores
from app.analyzers.severity import calculate_problem_severity, downgrade_severity
from app.analyzers.stock_states import (
    NO_VISIBLE_SUPPLY_REASON,
    SUPPLY_READY_MISMATCH_REASON,
    TEMPORARILY_UNAVAILABLE_LABEL,
    TEMPORARILY_UNAVAILABLE_REASON,
    enrich_stock_metrics,
    has_supply_ready_mismatch,
    has_wb_logistics_stock,
    stock_root_cause,
)
from app.collectors.cards import get_cards_list
from app.config import ABC_RULES, HEADERS
from app.constants.problem_labels import get_problem_label
from app.seller_config import SELLER_NAME
from app.storage.stub_storage import get_change_log, get_products
from app.wb_client import WBClient

SALES_FUNNEL_URL = (
    "https://seller-analytics-api.wildberries.ru"
    "/api/analytics/v3/sales-funnel/products"
)
MAX_FUNNEL_NM_IDS = 1000
REPORTS_DIR = Path("reports")
FUNNEL_REPORT_COLUMNS = [
    "sellerName",
    "date",
    "nmId",
    "vendorCode",
    "brandName",
    "title",
    "openCount",
    "cartCount",
    "orderCount",
    "orderSum",
    "addToCartPercent",
    "cartToOrderPercent",
    "localizationPercent",
    "avgPosition",
    "positionDelta",
    "visibilityScore",
    "searchVisibilityRisk",
    "wbStocks",
    "mpStocks",
    "realSellableStock",
    "incomingStock",
    "returningStock",
    "readyForSaleStock",
    "acceptanceStock",
    "transitStock",
    "stockState",
]

PROBLEMS_REPORT_COLUMNS = [
    "sellerName",
    "nmId",
    "vendorCode",
    "brandName",
    "title",
    "ABC",
    "productInCatalog",
    "productStatus",
    "problemType",
    "metric",
    "problemLabel",
    "selectedValue",
    "pastValue",
    "baselineType",
    "baselineValue",
    "dynamicPercent",
    "severity",
    "severityScore",
    "businessImpactScore",
    "isBelowAbcThreshold",
    "lostOrders",
    "lostOrderSum",
    "potentialRevenueLoss",
    "potentialOrdersLoss",
    "impactConfidence",
    "blockedRevenuePerDay",
    "blockedOrdersPerDay",
    "recommendation",
    "recentChanges",
    "avgPosition",
    "positionDelta",
    "visibilityScore",
    "searchVisibilityRisk",
    "rootCause",
    "realSellableStock",
    "incomingStock",
    "returningStock",
    "readyForSaleStock",
    "acceptanceStock",
    "transitStock",
    "stockState",
    "adsTrafficShare",
    "organicTrafficShare",
    "adsOrdersShare",
    "organicOrdersShare",
    "declineSource",
    "daysUntilOOS",
    "budgetWasteRisk",
    "reportTrustScore",
    "forecastEtaHours",
    "forecastConfidence",
    "forecastType",
    "forecastMessage",
]
PROBLEM_RULES = [
    {
        "problem_type": "openCount падение",
        "metric": "openCount",
        "dynamic_metric": "openCountDynamic",
        "threshold": -15,
        "recommendation": "проверить позиции, рекламу, наличие товара",
    },
    {
        "problem_type": "cartCount падение",
        "metric": "cartCount",
        "dynamic_metric": "cartCountDynamic",
        "threshold": -10,
        "recommendation": "проверить карточку, цену, отзывы",
    },
    {
        "problem_type": "orderCount падение",
        "metric": "orderCount",
        "dynamic_metric": "orderCountDynamic",
        "threshold": -10,
        "recommendation": "проверить доставку, остатки, цену",
    },
    {
        "problem_type": "orderSum падение",
        "metric": "orderSum",
        "dynamic_metric": "orderSumDynamic",
        "threshold": -10,
        "recommendation": "проверить заказы, цену, рекламу",
    },
    {
        "problem_type": "addToCartPercent падение",
        "metric": "addToCartPercent",
        "dynamic_metric": "addToCartPercentDynamic",
        "threshold": -10,
        "recommendation": "проверить главное фото, цену, УТП",
    },
    {
        "problem_type": "cartToOrderPercent падение",
        "metric": "cartToOrderPercent",
        "dynamic_metric": "cartToOrderPercentDynamic",
        "threshold": -10,
        "recommendation": "проверить доставку, цену, остатки",
    },
]
STOCK_PROBLEM_RECOMMENDATION = "проверить остатки и поставку на склады WB"


TRAFFIC_STABLE_THRESHOLD = -5
TRAFFIC_DECLINE_THRESHOLD = -10
CONVERSION_STABLE_ABS_THRESHOLD = 5


def _format_period(start_date, end_date):
    return {
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
    }


def _build_sales_funnel_payload(nm_ids):
    selected_day = datetime.now().date() - timedelta(days=1)
    past_day = selected_day - timedelta(days=1)

    return {
        "selectedPeriod": _format_period(selected_day, selected_day),
        "pastPeriod": _format_period(past_day, past_day),
        "nmIds": nm_ids[:MAX_FUNNEL_NM_IDS],
        "skipDeletedNm": False,
        "limit": min(len(nm_ids), MAX_FUNNEL_NM_IDS),
        "offset": 0,
    }


def _is_missing(value):
    if value is None:
        return True

    if isinstance(value, str) and value == "":
        return True

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _get_nested_value(data, path, default=None):
    if not isinstance(data, dict):
        return default

    if path in data:
        return data[path]

    current = data

    for key in path.split("."):
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def _first_present(data, paths, default=None):
    for path in paths:
        value = _get_nested_value(data, path, default=None)

        if not _is_missing(value):
            return value

    return default


def _extract_products(funnel_data):
    if isinstance(funnel_data, dict):
        products = _get_nested_value(funnel_data, "data.products")

        if isinstance(products, list):
            return products

        products = funnel_data.get("products")

        if isinstance(products, list):
            return products

        products = funnel_data.get("data")

        if isinstance(products, list):
            return products

    if isinstance(funnel_data, list):
        return funnel_data

    return []


def _flatten_history_products(products):
    rows = []

    for item in products:
        product = item.get("product", {}) if isinstance(item, dict) else {}
        history = item.get("history", []) if isinstance(item, dict) else []

        if not isinstance(history, list):
            continue

        for history_item in history:
            if not isinstance(history_item, dict):
                continue

            rows.append(
                {
                    "sellerName": SELLER_NAME,
                    "date": history_item.get("date"),
                    "nmId": product.get("nmId"),
                    "vendorCode": product.get("vendorCode"),
                    "brandName": product.get("brandName"),
                    "title": product.get("title"),
                    "openCount": history_item.get("openCount"),
                    "cartCount": history_item.get("cartCount"),
                    "orderCount": history_item.get("orderCount"),
                    "orderSum": history_item.get("orderSum"),
                    "addToCartPercent": _first_present(
                        history_item, ["addToCartPercent", "addToCartConversion"]
                    ),
                    "cartToOrderPercent": _first_present(
                        history_item, ["cartToOrderPercent", "cartToOrderConversion"]
                    ),
                    "localizationPercent": history_item.get("localizationPercent"),
                    "avgPosition": _first_present(
                        history_item, ["avgPosition", "averagePosition", "position"]
                    ),
                    "positionDelta": "",
                    "visibilityScore": _first_present(
                        history_item,
                        [
                            "visibilityScore",
                            "searchVisibilityScore",
                            "visibility_score",
                        ],
                    ),
                    "searchVisibilityRisk": "",
                    "wbStocks": _get_nested_value(product, "stocks.wb"),
                    "mpStocks": _get_nested_value(product, "stocks.mp"),
                }
            )

    return rows


def _to_number(value):
    if _is_missing(value):
        return None

    if isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        normalized = value.replace("%", "").replace(" ", "").replace(",", ".")

        try:
            return float(normalized)
        except ValueError:
            return None

    return None


def _format_problem_number(value):
    number = _to_number(value)

    if number is None:
        return "" if _is_missing(value) else value

    if isinstance(number, float) and number.is_integer():
        return int(number)

    return round(number, 2)


def _calculate_dynamic_percent(selected_value, past_value):
    selected_number = _to_number(selected_value)
    past_number = _to_number(past_value)

    if selected_number is None or past_number in (None, 0):
        return None

    return ((selected_number - past_number) / past_number) * 100


def _position_paths(period):
    return [
        f"statistic.{period}.avgPosition",
        f"statistics.{period}.avgPosition",
        f"{period}.avgPosition",
        f"avgPosition.{period}",
        f"statistic.{period}.position",
        f"statistics.{period}.position",
        f"{period}.position",
        f"position.{period}",
    ]


def _position_metrics(record):
    selected_position = _first_present(
        record,
        [
            *_position_paths("selected"),
            "avgPosition",
            "averagePosition",
            "position",
        ],
        default="",
    )
    past_position = _first_present(
        record,
        [
            *_position_paths("past"),
            "previousAvgPosition",
            "pastAvgPosition",
            "previousPosition",
            "pastPosition",
        ],
        default="",
    )
    selected_number = _to_number(selected_position)
    past_number = _to_number(past_position)

    position_delta = None
    visibility_score = _first_present(
        record,
        [
            "visibilityScore",
            "searchVisibilityScore",
            "visibility_score",
            "search_visibility_score",
        ],
        default=None,
    )
    risk = ""

    if selected_number is not None and past_number is not None:
        position_delta = selected_number - past_number
        if _to_number(visibility_score) is None and past_number != 0:
            visibility_score = ((past_number - selected_number) / past_number) * 100

        if position_delta > 0:
            risk = "POSITION_DROP"

    visibility_number = _to_number(visibility_score)
    if visibility_number is not None:
        if visibility_number <= -30:
            risk = "SEARCH_TRAFFIC_LOSS"
        elif visibility_number < 0 and not risk:
            risk = "VISIBILITY_DROP"

    return {
        "avgPosition": _format_problem_number(selected_position),
        "pastAvgPosition": _format_problem_number(past_position),
        "positionDelta": _format_problem_number(position_delta),
        "visibilityScore": _format_problem_number(visibility_score),
        "searchVisibilityRisk": risk,
    }


def _metric_paths(period, metric):
    conversion_paths = []

    if metric in {"addToCartPercent", "cartToOrderPercent"}:
        conversion_paths = [
            f"statistic.{period}.conversions.{metric}",
            f"{period}.conversions.{metric}",
            f"statistics.{period}.conversions.{metric}",
        ]

        if metric == "addToCartPercent":
            conversion_paths.extend(
                [
                    f"statistic.{period}.addToCartConversion",
                    f"{period}.addToCartConversion",
                ]
            )
        else:
            conversion_paths.extend(
                [
                    f"statistic.{period}.cartToOrderConversion",
                    f"{period}.cartToOrderConversion",
                ]
            )

    return [
        *conversion_paths,
        f"statistic.{period}.{metric}",
        f"statistics.{period}.{metric}",
        f"{period}.{metric}",
        f"{metric}.{period}",
    ]


def _dynamic_paths(metric, dynamic_metric):
    return [
        dynamic_metric,
        f"statistic.{dynamic_metric}",
        f"statistics.{dynamic_metric}",
        f"dynamics.{dynamic_metric}",
        f"dynamic.{dynamic_metric}",
        f"comparison.{dynamic_metric}",
        f"statistic.dynamics.{dynamic_metric}",
        f"statistics.dynamics.{dynamic_metric}",
        f"statistic.comparison.{dynamic_metric}",
        f"statistics.comparison.{dynamic_metric}",
        f"{metric}.dynamic",
        f"statistic.{metric}.dynamic",
        f"statistics.{metric}.dynamic",
    ]


def _problem_product_value(record, key):
    paths = {
        "nmId": ["product.nmId", "nmId", "nmID"],
        "vendorCode": ["product.vendorCode", "vendorCode"],
        "brandName": ["product.brandName", "brandName"],
        "title": ["product.title", "title"],
        "ABC": ["product.ABC", "ABC", "product.abc", "abc"],
        "productInCatalog": ["product.productInCatalog", "productInCatalog"],
        "productStatus": ["product.productStatus", "productStatus"],
        "openCount": _metric_paths("selected", "openCount") + ["openCount"],
        "orderCount": _metric_paths("selected", "orderCount") + ["orderCount"],
        "orderSum": _metric_paths("selected", "orderSum") + ["orderSum"],
        "wbStocks": ["product.stocks.wb", "stocks.wb", "wbStocks"],
        "realSellableStock": [
            "realSellableStock",
            "product.stocks.realSellable",
            "stocks.realSellable",
        ],
        "incomingStock": [
            "incomingStock",
            "product.stocks.incoming",
            "stocks.incoming",
        ],
        "returningStock": [
            "returningStock",
            "product.stocks.returning",
            "stocks.returning",
        ],
        "readyForSaleStock": [
            "readyForSaleStock",
            "product.stocks.readyForSale",
            "stocks.readyForSale",
        ],
        "acceptanceStock": [
            "acceptanceStock",
            "product.stocks.acceptance",
            "stocks.acceptance",
        ],
        "transitStock": ["transitStock", "product.stocks.transit", "stocks.transit"],
        "stockState": ["stockState", "product.stocks.state", "stocks.state"],
    }

    return _first_present(record, paths[key], default="")


def _normalize_nm_id(nm_id):
    if _is_missing(nm_id):
        return ""

    number = _to_number(nm_id)

    if number is not None:
        if isinstance(number, float) and number.is_integer():
            return str(int(number))

        return str(number)

    return str(nm_id).strip()


def _build_products_by_nm_id():
    products_by_nm_id = {}

    for product in get_products():
        nm_id = _normalize_nm_id(product.get("nmId"))

        if nm_id:
            products_by_nm_id[nm_id] = product

    return products_by_nm_id


def _product_metadata(record, products_by_nm_id):
    nm_id = _normalize_nm_id(_problem_product_value(record, "nmId"))

    return products_by_nm_id.get(nm_id, {})


def _product_abc(record, products_by_nm_id):
    enriched_abc = _problem_product_value(record, "ABC")

    if not _is_missing(enriched_abc):
        abc = str(enriched_abc).upper()
    else:
        abc = str(
            _product_metadata(record, products_by_nm_id).get("abc") or "C"
        ).upper()

    if abc == "UNKNOWN":
        return abc

    if abc not in ABC_RULES:
        return "C"

    return abc


def _abc_rules_key(abc):
    return "C" if abc == "UNKNOWN" else abc


def _product_in_catalog(record, products_by_nm_id):
    enriched_value = _problem_product_value(record, "productInCatalog")

    if isinstance(enriched_value, bool):
        return enriched_value

    if not _is_missing(enriched_value):
        return str(enriched_value).strip().lower() in {"true", "1", "yes", "да"}

    return bool(_product_metadata(record, products_by_nm_id))


def _product_status(record, products_by_nm_id):
    enriched_status = _problem_product_value(record, "productStatus")

    if not _is_missing(enriched_status):
        return enriched_status

    return _product_metadata(record, products_by_nm_id).get("status", "")


def _recommendation(record, products_by_nm_id, recommendation):
    if _product_in_catalog(record, products_by_nm_id):
        return recommendation

    return f"{recommendation}; внести товар в PRODUCTS и назначить ABC"


def _passes_abc_filter(record, products_by_nm_id):
    abc = _abc_rules_key(_product_abc(record, products_by_nm_id))
    rules = ABC_RULES[abc]
    open_count = _to_number(_problem_product_value(record, "openCount")) or 0
    order_count = _to_number(_problem_product_value(record, "orderCount")) or 0
    order_sum = _to_number(_problem_product_value(record, "orderSum")) or 0

    return (
        open_count >= rules["min_open_count"]
        and order_count >= rules["min_orders"]
        and order_sum >= rules["min_order_sum"]
    )


def _extract_problem_records(funnel_data):
    products = _extract_products(funnel_data)

    if not products:
        return []

    return pd.json_normalize(products, sep=".").to_dict("records")


def _parse_change_date(value):
    if _is_missing(value):
        return None

    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return None


def _format_change_log_item(change):
    change_date = str(change.get("date") or "").strip()
    change_type = str(change.get("changeType") or "").strip()
    comment = str(change.get("comment") or "").strip()

    if change_type and comment:
        change_text = f"{change_type} — {comment}"
    else:
        change_text = change_type or comment

    return f"{change_date}: {change_text}" if change_date else change_text


def _build_recent_changes_by_nm_id(days=7):
    today = datetime.now().date()
    start_date = today - timedelta(days=days)
    changes_by_nm_id = {}

    for change in get_change_log():
        change_date = _parse_change_date(change.get("date"))

        if change_date is None or change_date < start_date or change_date > today:
            continue

        nm_id = _normalize_nm_id(change.get("nmId"))

        if not nm_id:
            continue

        changes_by_nm_id.setdefault(nm_id, []).append(change)

    for changes in changes_by_nm_id.values():
        changes.sort(key=lambda item: item.get("date") or "", reverse=True)

    return {
        nm_id: "\n".join(_format_change_log_item(change) for change in changes)
        for nm_id, changes in changes_by_nm_id.items()
    }


def _recent_changes(record, recent_changes_by_nm_id):
    nm_id = _normalize_nm_id(_problem_product_value(record, "nmId"))

    return recent_changes_by_nm_id.get(nm_id, "")


def _impact_confidence(baseline_type, baseline_value=None):
    if _to_number(baseline_value) is None:
        return "INSUFFICIENT_HISTORY"
    if baseline_type == "avg_7d":
        return "HIGH"
    if baseline_type == "avg_3d":
        return "MEDIUM"
    if baseline_type == "fallback_previous_day":
        return "LOW"
    return "INSUFFICIENT_HISTORY"


def _average_check(order_sum, order_count):
    order_sum_number = _to_number(order_sum)
    order_count_number = _to_number(order_count)
    if order_sum_number is None or order_count_number in (None, 0):
        return None
    return order_sum_number / order_count_number


def _selected_metric(record, metric):
    return _first_present(record, _metric_paths("selected", metric), default="")


def _safe_share(numerator, denominator):
    denominator_number = _to_number(denominator)

    if denominator_number in (None, 0):
        return ""

    return _format_problem_number((_to_number(numerator) or 0) / denominator_number)


def _ads_metric(row, metric):
    value = row.get(metric)
    if _is_missing(value):
        value = row.get(f"selected{metric[0].upper()}{metric[1:]}")
    return _to_number(value) or 0


def _ads_previous_metric(row, metric):
    return _to_number(row.get(f"previous{metric[0].upper()}{metric[1:]}")) or 0


def _build_ads_attribution_by_nm_id(ads_rows):
    attribution_by_nm_id = {}

    for row in ads_rows or []:
        if not isinstance(row, dict):
            continue

        nm_id = _normalize_nm_id(row.get("nmId"))

        if not nm_id:
            continue

        attribution = attribution_by_nm_id.setdefault(
            nm_id,
            {
                "adsClicks": 0,
                "previousAdsClicks": 0,
                "adsOrders": 0,
                "previousAdsOrders": 0,
            },
        )
        attribution["adsClicks"] += _ads_metric(row, "clicks")
        attribution["previousAdsClicks"] += _ads_previous_metric(row, "clicks")
        attribution["adsOrders"] += _ads_metric(row, "orders")
        attribution["previousAdsOrders"] += _ads_previous_metric(row, "orders")

    return attribution_by_nm_id


def _metric_current(record, metric):
    return _to_number(_first_present(record, _metric_paths("selected", metric)))


def _metric_past(record, metric):
    return _to_number(_first_present(record, _metric_paths("past", metric)))


def _dynamic_or_none(current_value, past_value):
    if current_value is None or past_value in (None, 0):
        return None

    return ((current_value - past_value) / past_value) * 100


def _is_declining(dynamic_value, threshold=TRAFFIC_DECLINE_THRESHOLD):
    return dynamic_value is not None and dynamic_value <= threshold


def _is_stable(dynamic_value, threshold=TRAFFIC_STABLE_THRESHOLD):
    return dynamic_value is not None and dynamic_value >= threshold


def _build_attribution_fields(record, attribution):
    attribution = attribution or {}
    open_count = _metric_current(record, "openCount")
    past_open_count = _metric_past(record, "openCount")
    order_count = _metric_current(record, "orderCount")
    past_order_count = _metric_past(record, "orderCount")
    cart_to_order = _metric_current(record, "cartToOrderPercent")
    past_cart_to_order = _metric_past(record, "cartToOrderPercent")
    ads_clicks = attribution.get("adsClicks", 0)
    past_ads_clicks = attribution.get("previousAdsClicks", 0)
    ads_orders = attribution.get("adsOrders", 0)

    organic_traffic = max((open_count or 0) - ads_clicks, 0)
    past_organic_traffic = max((past_open_count or 0) - past_ads_clicks, 0)
    factors = []

    open_dynamic = _dynamic_or_none(open_count, past_open_count)
    ads_clicks_dynamic = _dynamic_or_none(ads_clicks, past_ads_clicks)
    organic_dynamic = _dynamic_or_none(organic_traffic, past_organic_traffic)
    order_dynamic = _dynamic_or_none(order_count, past_order_count)
    cart_to_order_dynamic = _dynamic_or_none(cart_to_order, past_cart_to_order)

    if _to_number(_problem_product_value(record, "realSellableStock")) == 0:
        factors.append("STOCK_DECLINE")

    if (
        _is_declining(ads_clicks_dynamic)
        and _is_declining(open_dynamic)
        and _is_stable(organic_dynamic)
    ):
        factors.append("ADS_DECLINE")

    if _is_stable(ads_clicks_dynamic) and _is_declining(open_dynamic):
        factors.append("ORGANIC_DECLINE")

    if (
        open_dynamic is not None
        and abs(open_dynamic) <= CONVERSION_STABLE_ABS_THRESHOLD
        and _is_declining(order_dynamic)
        and _is_declining(cart_to_order_dynamic)
    ):
        factors.append("CONVERSION_DECLINE")

    unique_factors = list(dict.fromkeys(factors))
    if len(unique_factors) > 1:
        decline_source = "MIXED_DECLINE"
    elif unique_factors:
        decline_source = unique_factors[0]
    elif open_count in (None, 0) and order_count in (None, 0):
        decline_source = "INSUFFICIENT_DATA"
    else:
        decline_source = "INSUFFICIENT_DATA"

    return {
        "adsTrafficShare": _safe_share(ads_clicks, open_count),
        "organicTrafficShare": _safe_share(organic_traffic, open_count),
        "adsOrdersShare": _safe_share(ads_orders, order_count),
        "organicOrdersShare": _safe_share(
            max((order_count or 0) - ads_orders, 0), order_count
        ),
        "declineSource": decline_source,
    }


def _build_business_impact(
    record, metric, selected_value, baseline_value, baseline_type
):
    selected_number = _to_number(selected_value)
    baseline_number = _to_number(baseline_value)
    confidence = _impact_confidence(baseline_type, baseline_value)
    impact = {
        "potentialRevenueLoss": "",
        "potentialOrdersLoss": "",
        "impactConfidence": confidence,
        "blockedRevenuePerDay": "",
        "blockedOrdersPerDay": "",
    }

    if confidence == "INSUFFICIENT_HISTORY" or selected_number is None:
        return impact

    order_count = _selected_metric(record, "orderCount")
    order_sum = _selected_metric(record, "orderSum")
    avg_check = _average_check(order_sum, order_count)

    if avg_check is None and metric == "orderCount":
        avg_check = _average_check(_selected_metric(record, "orderSum"), selected_value)
    if avg_check is None and metric == "orderSum":
        avg_check = _average_check(
            selected_value, _selected_metric(record, "orderCount")
        )

    if metric == "orderCount":
        orders_loss = max(baseline_number - selected_number, 0)
        impact["potentialOrdersLoss"] = _format_problem_number(orders_loss)
        if avg_check is not None:
            impact["potentialRevenueLoss"] = _format_problem_number(
                orders_loss * avg_check
            )
    elif metric == "orderSum":
        revenue_loss = max(baseline_number - selected_number, 0)
        impact["potentialRevenueLoss"] = _format_problem_number(revenue_loss)
        if avg_check is not None:
            impact["potentialOrdersLoss"] = _format_problem_number(
                revenue_loss / avg_check
            )
    elif metric == "cartToOrderPercent":
        traffic = _to_number(_selected_metric(record, "cartCount"))
        if traffic is not None:
            expected_orders = traffic * baseline_number / 100
            actual_orders = traffic * selected_number / 100
            orders_loss = max(expected_orders - actual_orders, 0)
            impact["potentialOrdersLoss"] = _format_problem_number(orders_loss)
            if avg_check is not None:
                impact["potentialRevenueLoss"] = _format_problem_number(
                    orders_loss * avg_check
                )
    elif metric == "addToCartPercent":
        traffic = _to_number(_selected_metric(record, "openCount"))
        cart_to_order = _to_number(_selected_metric(record, "cartToOrderPercent"))
        if traffic is not None and cart_to_order is not None:
            expected_carts = traffic * baseline_number / 100
            actual_carts = traffic * selected_number / 100
            orders_loss = max(expected_carts - actual_carts, 0) * cart_to_order / 100
            impact["potentialOrdersLoss"] = _format_problem_number(orders_loss)
            if avg_check is not None:
                impact["potentialRevenueLoss"] = _format_problem_number(
                    orders_loss * avg_check
                )

    return impact


def _build_stock_business_impact(record, history_baselines):
    order_baseline = (history_baselines or {}).get(
        f"avg_{HISTORY_BASELINE_KEYS['orderCount']}_{'7d' if (history_baselines or {}).get('baselineType') == 'avg_7d' else '3d'}"
    )
    revenue_baseline = (history_baselines or {}).get(
        f"avg_{HISTORY_BASELINE_KEYS['orderSum']}_{'7d' if (history_baselines or {}).get('baselineType') == 'avg_7d' else '3d'}"
    )
    baseline_type = (history_baselines or {}).get("baselineType")
    confidence = _impact_confidence(baseline_type, order_baseline or revenue_baseline)
    return {
        "potentialRevenueLoss": (
            _format_problem_number(revenue_baseline)
            if revenue_baseline is not None
            else ""
        ),
        "potentialOrdersLoss": (
            _format_problem_number(order_baseline) if order_baseline is not None else ""
        ),
        "impactConfidence": confidence,
        "blockedRevenuePerDay": (
            _format_problem_number(revenue_baseline)
            if revenue_baseline is not None
            else ""
        ),
        "blockedOrdersPerDay": (
            _format_problem_number(order_baseline) if order_baseline is not None else ""
        ),
    }


def _build_problem_row(
    record,
    rule,
    selected_value,
    past_value,
    dynamic_percent,
    products_by_nm_id,
    recent_changes,
    baseline_type="fallback_previous_day",
    baseline_value=None,
):
    abc = _product_abc(record, products_by_nm_id)
    severity_fields = calculate_problem_severity(
        rule["metric"], selected_value, past_value, dynamic_percent, abc
    )
    position_metrics = _position_metrics(record)
    root_cause = ""
    if rule["metric"] in {"addToCartPercent", "openCount"} and dynamic_percent < 0:
        if _to_number(position_metrics.get("positionDelta")) is not None:
            if _to_number(position_metrics.get("positionDelta")) > 0:
                root_cause = "Просадка связана с потерей позиции в выдаче"
            else:
                root_cause = "Проблема вероятно связана с карточкой или рекламой"
        elif rule["metric"] == "openCount":
            root_cause = "SEARCH_TRAFFIC_LOSS"

    return {
        "sellerName": SELLER_NAME,
        "nmId": _problem_product_value(record, "nmId"),
        "vendorCode": _problem_product_value(record, "vendorCode"),
        "brandName": _problem_product_value(record, "brandName"),
        "title": _problem_product_value(record, "title"),
        "ABC": abc,
        "productInCatalog": _product_in_catalog(record, products_by_nm_id),
        "productStatus": _product_status(record, products_by_nm_id),
        "problemType": rule["problem_type"],
        "metric": rule["metric"],
        "problemLabel": get_problem_label(rule["metric"]),
        "selectedValue": _format_problem_number(selected_value),
        "pastValue": _format_problem_number(past_value),
        "baselineType": baseline_type,
        "baselineValue": _format_problem_number(
            past_value if baseline_value is None else baseline_value
        ),
        "dynamicPercent": round(dynamic_percent, 2),
        **severity_fields,
        **_build_business_impact(
            record,
            rule["metric"],
            selected_value,
            past_value if baseline_value is None else baseline_value,
            baseline_type,
        ),
        "recommendation": _recommendation(
            record, products_by_nm_id, rule["recommendation"]
        ),
        "recentChanges": recent_changes,
        **position_metrics,
        "rootCause": root_cause,
    }


HISTORY_METRIC_ALIASES = {
    "openCount": ["openCount", "open_count"],
    "cartCount": ["cartCount", "cart_count"],
    "orderCount": ["orderCount", "order_count"],
    "orderSum": ["orderSum", "order_sum"],
    "addToCartPercent": [
        "addToCartPercent",
        "add_to_cart_percent",
        "addToCartConversion",
        "add_to_cart_conversion",
    ],
    "cartToOrderPercent": [
        "cartToOrderPercent",
        "cart_to_order_percent",
        "cartToOrderConversion",
        "cart_to_order_conversion",
    ],
}

HISTORY_BASELINE_METRICS = list(HISTORY_METRIC_ALIASES.keys())

HISTORY_BASELINE_KEYS = {
    metric: HISTORY_METRIC_ALIASES[metric][1] for metric in HISTORY_BASELINE_METRICS
}


def _history_metric_value(row, metric):
    return _first_present(
        row,
        HISTORY_METRIC_ALIASES.get(metric, [metric]),
        default=None,
    )


def _average_history_metric(history_rows, metric):
    values = [
        _to_number(_history_metric_value(row, metric))
        for row in history_rows
        if _to_number(_history_metric_value(row, metric)) is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)


def _history_baselines(history_rows):
    history_rows = history_rows or []
    history_3d = history_rows[:3]
    history_7d = history_rows[:7]
    rows_loaded = len(history_rows)
    baselines = {
        "rowsLoaded": rows_loaded,
        "baselineType": _baseline_type_for_rows_loaded(rows_loaded),
    }

    for metric in HISTORY_BASELINE_METRICS:
        baseline_key = HISTORY_BASELINE_KEYS[metric]
        baselines[f"avg_{baseline_key}_3d"] = _average_history_metric(
            history_3d, metric
        )
        baselines[f"avg_{baseline_key}_7d"] = _average_history_metric(
            history_7d, metric
        )

    return baselines


def _baseline_type_for_rows_loaded(rows_loaded):
    if rows_loaded >= 7:
        return "avg_7d"

    if rows_loaded >= 3:
        return "avg_3d"

    return "fallback_previous_day"


def _metric_baseline(record, rule, history_baselines=None):
    metric = rule["metric"]
    selected_value = _first_present(
        record, _metric_paths("selected", metric), default=""
    )
    rows_loaded = (history_baselines or {}).get("rowsLoaded", 0)

    if metric in HISTORY_BASELINE_KEYS and rows_loaded >= 3:
        baseline_type = _baseline_type_for_rows_loaded(rows_loaded)
        baseline_days = "7d" if baseline_type == "avg_7d" else "3d"
        baseline_key = HISTORY_BASELINE_KEYS[metric]
        baseline_value = (history_baselines or {}).get(
            f"avg_{baseline_key}_{baseline_days}"
        )
        dynamic_percent = _calculate_dynamic_percent(selected_value, baseline_value)
        return selected_value, baseline_value, dynamic_percent, baseline_type

    _, past_value, dynamic_percent = _metric_dynamic_percent(record, rule)
    return selected_value, past_value, dynamic_percent, "fallback_previous_day"


def _metric_dynamic_percent(record, rule):
    selected_value = _first_present(
        record, _metric_paths("selected", rule["metric"]), default=""
    )
    past_value = _first_present(
        record, _metric_paths("past", rule["metric"]), default=""
    )
    dynamic_value = _first_present(
        record,
        _dynamic_paths(rule["metric"], rule["dynamic_metric"]),
        default=None,
    )
    dynamic_percent = _to_number(dynamic_value)

    if dynamic_percent is None:
        dynamic_percent = _calculate_dynamic_percent(selected_value, past_value)

    return selected_value, past_value, dynamic_percent


def _apply_abc_priority(problem_rows, is_below_abc_threshold):
    for problem_row in problem_rows:
        problem_row["isBelowAbcThreshold"] = is_below_abc_threshold

        if is_below_abc_threshold:
            problem_row["severity"] = downgrade_severity(problem_row.get("severity"))

    return problem_rows


def _build_record_problem_rows(
    record,
    products_by_nm_id,
    recent_changes="",
    history_baselines=None,
    supply_stock_metrics=None,
    attribution=None,
):
    record_problem_rows = []
    attribution_fields = _build_attribution_fields(record, attribution)

    for rule in PROBLEM_RULES:
        selected_value, past_value, dynamic_percent, baseline_type = _metric_baseline(
            record, rule, history_baselines
        )

        if dynamic_percent is None or dynamic_percent > rule["threshold"]:
            continue

        problem_row = _build_problem_row(
            record,
            rule,
            selected_value,
            past_value,
            dynamic_percent,
            products_by_nm_id,
            recent_changes,
            baseline_type=baseline_type,
            baseline_value=past_value,
        )
        problem_row.update(attribution_fields)
        record_problem_rows.append(problem_row)

    wb_stocks = _to_number(_problem_product_value(record, "wbStocks"))
    stock_metrics = enrich_stock_metrics(
        {
            key: _problem_product_value(record, key)
            for key in (
                "wbStocks",
                "realSellableStock",
                "incomingStock",
                "returningStock",
                "readyForSaleStock",
                "acceptanceStock",
                "transitStock",
                "stockState",
            )
        },
        supply_stock_metrics,
    )
    real_sellable_stock = _to_number(stock_metrics.get("realSellableStock"))

    if real_sellable_stock == 0:
        stock_attribution_fields = {**attribution_fields}
        stock_attribution_fields["declineSource"] = (
            "MIXED_DECLINE"
            if stock_attribution_fields.get("declineSource")
            not in ("", "INSUFFICIENT_DATA", "STOCK_DECLINE")
            else "STOCK_DECLINE"
        )
        abc = _product_abc(record, products_by_nm_id)
        metric_name = "realSellableStock"
        selected_stock_value = _format_problem_number(real_sellable_stock)
        is_logistics_gap = wb_stocks == 0 and has_wb_logistics_stock(stock_metrics)
        problem_type = "sellableOutOfStock"
        problem_label = get_problem_label(metric_name)
        recommendation = STOCK_PROBLEM_RECOMMENDATION
        severity_fields = calculate_problem_severity(metric_name, 0, "", "", abc)
        if is_logistics_gap:
            problem_type = stock_metrics.get("stockRiskType") or "acceptanceDelay"
            problem_label = TEMPORARILY_UNAVAILABLE_LABEL
            recommendation = TEMPORARILY_UNAVAILABLE_REASON
            severity_fields["severity"] = "medium"
        elif has_supply_ready_mismatch(stock_metrics):
            recommendation = SUPPLY_READY_MISMATCH_REASON
        elif stock_metrics.get("stockState") == "BLOCKED":
            recommendation = NO_VISIBLE_SUPPLY_REASON
        record_problem_rows.append(
            {
                "sellerName": SELLER_NAME,
                "nmId": _problem_product_value(record, "nmId"),
                "vendorCode": _problem_product_value(record, "vendorCode"),
                "brandName": _problem_product_value(record, "brandName"),
                "title": _problem_product_value(record, "title"),
                "ABC": abc,
                "productInCatalog": _product_in_catalog(record, products_by_nm_id),
                "productStatus": _product_status(record, products_by_nm_id),
                "problemType": problem_type,
                "metric": metric_name,
                "problemLabel": problem_label,
                "selectedValue": selected_stock_value,
                "pastValue": "",
                "baselineType": "stock_check",
                "baselineValue": "",
                "dynamicPercent": "",
                **severity_fields,
                **_build_stock_business_impact(record, history_baselines),
                "recommendation": _recommendation(
                    record, products_by_nm_id, recommendation
                ),
                "recentChanges": recent_changes,
                "rootCause": stock_root_cause(stock_metrics),
                **stock_metrics,
                **stock_attribution_fields,
            }
        )

    return record_problem_rows


def count_sku_ignored_by_abc_filter(funnel_data):
    below_threshold_problem_count = 0
    products_by_nm_id = _build_products_by_nm_id()

    for record in _extract_problem_records(funnel_data):
        record_problem_rows = _build_record_problem_rows(record, products_by_nm_id)

        if record_problem_rows and not _passes_abc_filter(record, products_by_nm_id):
            below_threshold_problem_count += len(record_problem_rows)

    return below_threshold_problem_count


def build_top_funnel_drop_signals(funnel_data, limit=5):
    signals_by_sku = {}

    for record in _extract_problem_records(funnel_data):
        nm_id = _problem_product_value(record, "nmId")
        signal_key = nm_id or _problem_product_value(record, "title")

        for rule in PROBLEM_RULES:
            selected_value, past_value, dynamic_percent = _metric_dynamic_percent(
                record, rule
            )

            if dynamic_percent is None or dynamic_percent >= 0:
                continue

            signal = {
                "nmId": nm_id,
                "vendorCode": _problem_product_value(record, "vendorCode"),
                "title": _problem_product_value(record, "title"),
                "metric": rule["metric"],
                "problemLabel": get_problem_label(rule["metric"]),
                "selectedValue": _format_problem_number(selected_value),
                "pastValue": _format_problem_number(past_value),
                "dynamicPercent": round(dynamic_percent, 2),
            }

            if (
                signal_key not in signals_by_sku
                or signal["dynamicPercent"]
                < signals_by_sku[signal_key]["dynamicPercent"]
            ):
                signals_by_sku[signal_key] = signal

    return sorted(signals_by_sku.values(), key=lambda signal: signal["dynamicPercent"])[
        :limit
    ]


def _summary_metric_paths(period, metric):
    period_aliases = {
        "selected": ["selectedPeriod", "selected"],
        "past": ["pastPeriod", "past"],
    }[period]
    paths = []

    for period_alias in period_aliases:
        paths.extend(
            [
                f"statistic.{period_alias}.{metric}",
                f"statistics.{period_alias}.{metric}",
                f"{period_alias}.{metric}",
            ]
        )

    if period == "selected":
        paths.append(metric)

    return paths


def _calculate_summary_dynamic(current_value, past_value):
    if past_value > 0:
        return ((current_value - past_value) / past_value) * 100

    if current_value > 0:
        return 100

    return 0


def _sum_summary_metric(records, period, metric):
    total = 0

    for record in records:
        value = _first_present(record, _summary_metric_paths(period, metric), default=0)
        total += _to_number(value) or 0

    return int(total) if float(total).is_integer() else round(float(total), 2)


def calculate_funnel_summary_dynamics(funnel_data):
    records = _extract_problem_records(funnel_data)
    summary = {}
    metric_keys = {
        "openCount": "OpenCount",
        "cartCount": "CartCount",
        "orderCount": "OrderCount",
        "orderSum": "OrderSum",
    }

    for metric, key_suffix in metric_keys.items():
        selected_value = _sum_summary_metric(records, "selected", metric)
        past_value = _sum_summary_metric(records, "past", metric)
        dynamic_value = _calculate_summary_dynamic(selected_value, past_value)

        summary[f"selected{key_suffix}"] = selected_value
        summary[f"past{key_suffix}"] = past_value
        summary[f"{metric}Dynamic"] = round(dynamic_value, 2)

    return summary


def _load_history_baselines(seller_id, records):
    try:
        from app.storage.storage_factory import get_storage
    except ImportError:
        return {}

    if seller_id in (None, ""):
        return {}

    storage = get_storage()

    if not hasattr(storage, "get_funnel_history"):
        return {}

    baselines_by_nm_id = {}
    total_rows_loaded = 0
    baseline_type_counts = {
        "avg_7d": 0,
        "avg_3d": 0,
        "fallback_previous_day": 0,
    }

    for record in records:
        nm_id = _normalize_nm_id(_problem_product_value(record, "nmId"))

        if not nm_id or nm_id in baselines_by_nm_id:
            continue

        history_rows = storage.get_funnel_history(seller_id, nm_id, 7)
        baselines = _history_baselines(history_rows)
        baselines_by_nm_id[nm_id] = baselines
        total_rows_loaded += baselines["rowsLoaded"]
        baseline_type_counts[baselines["baselineType"]] += 1

    print("HISTORICAL ANALYTICS:")
    print(f"history rows loaded: {total_rows_loaded}")
    print(f"baseline avg_7d SKU: {baseline_type_counts['avg_7d']}")
    print(f"baseline avg_3d SKU: {baseline_type_counts['avg_3d']}")
    print(
        "baseline fallback_previous_day SKU: "
        f"{baseline_type_counts['fallback_previous_day']}"
    )

    return baselines_by_nm_id


def analyze_funnel_problems(
    funnel_data, seller_id=None, supply_stock_metrics_by_nm_id=None, ads_rows=None
):
    problem_rows = []
    below_threshold_problem_count = 0
    products_by_nm_id = _build_products_by_nm_id()
    recent_changes_by_nm_id = _build_recent_changes_by_nm_id()

    records = _extract_problem_records(funnel_data)
    history_baselines_by_nm_id = _load_history_baselines(seller_id, records)
    ads_attribution_by_nm_id = _build_ads_attribution_by_nm_id(ads_rows)

    for record in records:
        recent_changes = _recent_changes(record, recent_changes_by_nm_id)
        nm_id = _normalize_nm_id(_problem_product_value(record, "nmId"))
        record_problem_rows = _build_record_problem_rows(
            record,
            products_by_nm_id,
            recent_changes,
            history_baselines_by_nm_id.get(nm_id),
            (supply_stock_metrics_by_nm_id or {}).get(nm_id),
            ads_attribution_by_nm_id.get(nm_id),
        )

        if not record_problem_rows:
            continue

        is_below_abc_threshold = not _passes_abc_filter(record, products_by_nm_id)
        _apply_abc_priority(record_problem_rows, is_below_abc_threshold)

        if is_below_abc_threshold:
            below_threshold_problem_count += len(record_problem_rows)

        problem_rows.extend(record_problem_rows)

    enrich_business_impact_scores(problem_rows)
    priority_problem_count = len(problem_rows) - below_threshold_problem_count

    print("ABC PRIORITY FILTER:")
    print(f"below threshold problems: {below_threshold_problem_count}")
    print(f"priority problems: {priority_problem_count}")
    print(f"total problems: {len(problem_rows)}")

    return pd.DataFrame(problem_rows, columns=PROBLEMS_REPORT_COLUMNS).fillna("")


def _print_problems_summary(dataframe):
    print("=" * 50)
    print("АНАЛИЗ ПРОСАДОК ПО FUNNEL")
    print(f"Найдено проблем: {len(dataframe)}")

    if dataframe.empty:
        print("Проблем не найдено")
        print("=" * 50)
        return

    print("Топ-5 проблем:")

    for index, row in dataframe.head(5).iterrows():
        dynamic = row["dynamicPercent"]
        dynamic_text = f"{dynamic}%" if dynamic != "" else "n/a"
        print(
            f"{index + 1}. nmId={row['nmId']} | {row['problemType']} | "
            f"{row['problemLabel']}: {row['selectedValue']} vs {row['pastValue']} "
            f"({dynamic_text}) | {row['recommendation']}"
        )

    print("=" * 50)


def flatten_sales_funnel_data(funnel_data):
    products = _extract_products(funnel_data)
    history_rows = _flatten_history_products(products)

    if history_rows:
        return pd.DataFrame(history_rows, columns=FUNNEL_REPORT_COLUMNS).fillna("")

    if products:
        normalized = pd.json_normalize(products, sep=".")
    else:
        normalized = pd.DataFrame()

    rows = []

    for record in normalized.to_dict("records"):
        selected_period_start = _first_present(
            record,
            [
                "statistic.selected.period.start",
                "selected.period.start",
                "period.start",
                "date",
            ],
            default="",
        )
        selected_period_end = _first_present(
            record,
            [
                "statistic.selected.period.end",
                "selected.period.end",
                "period.end",
            ],
            default=selected_period_start,
        )
        report_date = selected_period_start

        if selected_period_end and selected_period_end != selected_period_start:
            report_date = f"{selected_period_start} — {selected_period_end}"

        rows.append(
            {
                **{
                    "sellerName": SELLER_NAME,
                    "date": report_date,
                    "nmId": _first_present(record, ["product.nmId", "nmId", "nmID"]),
                    "vendorCode": _first_present(
                        record, ["product.vendorCode", "vendorCode"]
                    ),
                    "brandName": _first_present(
                        record, ["product.brandName", "brandName"]
                    ),
                    "title": _first_present(record, ["product.title", "title"]),
                },
                **{
                    key: value
                    for key, value in {
                        "openCount": _first_present(
                            record,
                            [
                                "statistic.selected.openCount",
                                "selected.openCount",
                                "openCount",
                            ],
                        ),
                        "cartCount": _first_present(
                            record,
                            [
                                "statistic.selected.cartCount",
                                "selected.cartCount",
                                "cartCount",
                            ],
                        ),
                        "orderCount": _first_present(
                            record,
                            [
                                "statistic.selected.orderCount",
                                "selected.orderCount",
                                "orderCount",
                            ],
                        ),
                    }.items()
                },
            }
        )

        rows[-1].update(
            {
                "orderSum": _first_present(
                    record,
                    ["statistic.selected.orderSum", "selected.orderSum", "orderSum"],
                ),
                "addToCartPercent": _first_present(
                    record,
                    [
                        "statistic.selected.conversions.addToCartPercent",
                        "selected.conversions.addToCartPercent",
                        "conversions.addToCartPercent",
                        "addToCartPercent",
                        "addToCartConversion",
                    ],
                ),
                "cartToOrderPercent": _first_present(
                    record,
                    [
                        "statistic.selected.conversions.cartToOrderPercent",
                        "selected.conversions.cartToOrderPercent",
                        "conversions.cartToOrderPercent",
                        "cartToOrderPercent",
                        "cartToOrderConversion",
                    ],
                ),
                "localizationPercent": _first_present(
                    record,
                    [
                        "statistic.selected.localizationPercent",
                        "selected.localizationPercent",
                        "localizationPercent",
                    ],
                ),
                **{
                    key: value
                    for key, value in _position_metrics(record).items()
                    if key != "pastAvgPosition"
                },
                "wbStocks": _first_present(record, ["product.stocks.wb", "stocks.wb"]),
                "mpStocks": _first_present(record, ["product.stocks.mp", "stocks.mp"]),
                "realSellableStock": _first_present(
                    record, ["realSellableStock", "product.stocks.realSellable"]
                ),
                "incomingStock": _first_present(
                    record, ["incomingStock", "product.stocks.incoming"]
                ),
                "returningStock": _first_present(
                    record, ["returningStock", "product.stocks.returning"]
                ),
                "readyForSaleStock": _first_present(
                    record, ["readyForSaleStock", "product.stocks.readyForSale"]
                ),
                "acceptanceStock": _first_present(
                    record, ["acceptanceStock", "product.stocks.acceptance"]
                ),
                "transitStock": _first_present(
                    record, ["transitStock", "product.stocks.transit"]
                ),
                "stockState": _first_present(
                    record, ["stockState", "product.stocks.state"]
                ),
            }
        )

    report = pd.DataFrame(rows, columns=FUNNEL_REPORT_COLUMNS)

    if report.empty:
        report = pd.DataFrame(columns=FUNNEL_REPORT_COLUMNS)

    return report.fillna("")


def _adjust_worksheet_layout(worksheet, dataframe):
    worksheet.freeze_panes = "A2"

    for column_index, column_name in enumerate(dataframe.columns, start=1):
        values = dataframe[column_name].astype(str).tolist()
        max_length = max([len(str(column_name)), *(len(value) for value in values)])
        adjusted_width = min(max_length + 2, 60)
        worksheet.column_dimensions[
            worksheet.cell(row=1, column=column_index).column_letter
        ].width = adjusted_width


def save_funnel_problems_report(
    funnel_data,
    seller_id=None,
    supply_stock_metrics_by_nm_id=None,
    ads_rows=None,
    predictive_forecasts=None,
):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report_date = datetime.now().date().strftime("%Y_%m_%d")
    report_path = REPORTS_DIR / f"problems_{report_date}.xlsx"
    dataframe = analyze_funnel_problems(
        funnel_data,
        seller_id=seller_id,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
        ads_rows=ads_rows,
    )

    if predictive_forecasts:
        forecast_dataframe = pd.DataFrame(predictive_forecasts)
        dataframe = pd.concat([dataframe, forecast_dataframe], ignore_index=True)
        dataframe = dataframe.reindex(columns=PROBLEMS_REPORT_COLUMNS).fillna("")

    _print_problems_summary(dataframe)

    print("СОХРАНЯЕМ ОТЧЁТ ПО ПРОБЛЕМАМ")
    print(f"Папка отчётов: {REPORTS_DIR}")
    print(f"Файл отчёта: {report_path}")
    print(f"Строк в отчёте: {len(dataframe)}")
    print(f"Колонки: {', '.join(PROBLEMS_REPORT_COLUMNS)}")

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="problems", index=False)
        worksheet = writer.sheets["problems"]
        _adjust_worksheet_layout(worksheet, dataframe)

    print("XLSX отчёт по проблемам сохранён")
    print("=" * 50)

    return report_path


def save_sales_funnel_report(funnel_data):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report_date = datetime.now().date().strftime("%Y_%m_%d")
    report_path = REPORTS_DIR / f"funnel_{report_date}.xlsx"
    dataframe = flatten_sales_funnel_data(funnel_data)

    print("=" * 50)
    print("СОХРАНЯЕМ FUNNEL XLSX ОТЧЁТ")
    print(f"Папка отчётов: {REPORTS_DIR}")
    print(f"Файл отчёта: {report_path}")
    print(f"Строк в отчёте: {len(dataframe)}")
    print(f"Колонки: {', '.join(FUNNEL_REPORT_COLUMNS)}")

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="funnel", index=False)
        worksheet = writer.sheets["funnel"]
        _adjust_worksheet_layout(worksheet, dataframe)

    print("Funnel XLSX отчёт сохранён")
    print("=" * 50)

    return report_path


def collect_sales_funnel():
    client = WBClient(HEADERS)

    cards_data = get_cards_list()

    if not cards_data:
        print("Не удалось получить карточки")
        return None

    cards = cards_data.get("cards", [])

    nm_ids = []

    for card in cards:
        nm_id = card.get("nmID")

        if nm_id:
            nm_ids.append(nm_id)

    print(f"Найдено nmIDs: {len(nm_ids)}")

    if not nm_ids:
        print("Список nmIDs пуст")
        return None

    payload = _build_sales_funnel_payload(nm_ids)

    print("Отправляем запрос в funnel API")
    print("selectedPeriod:", payload["selectedPeriod"])
    print("pastPeriod:", payload["pastPeriod"])
    print("limit:", payload["limit"])

    data = client.request(
        method="POST",
        url=SALES_FUNNEL_URL,
        json_data=payload,
    )

    return data
