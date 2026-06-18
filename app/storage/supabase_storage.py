import os
from datetime import date, datetime
from decimal import Decimal
from urllib.parse import urlsplit, urlunsplit

from supabase import create_client

_STORAGE_STATUS = {"mode": "supabase", "configured": True}
_CLIENT = None


def _supabase_url():
    raw_url = (os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
    parsed_url = urlsplit(raw_url)
    return urlunsplit(
        (
            parsed_url.scheme,
            parsed_url.netloc,
            "",
            "",
            "",
        )
    )


def _get_client():
    global _CLIENT

    if _CLIENT is None:
        _CLIENT = create_client(
            _supabase_url(),
            os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        )

    return _CLIENT


def _execute_read(query, table_name):
    try:
        response = query.execute()
    except Exception as error:
        print(f"WARNING: Supabase read failed for {table_name}: {error}")
        return []

    return response.data or []


def _execute_write(query, table_name):
    try:
        query.execute()
    except Exception as error:
        error_message = str(error)
        if (
            table_name == "problems"
            and "business_impact_score" in error_message
            and "schema cache" in error_message.lower()
        ):
            print(
                "SUPABASE SCHEMA MISSING COLUMN: "
                "problems.business_impact_score\n"
                "Apply migration: "
                "supabase/migrations/add_problem_business_impact_score.sql"
            )
        print(f"WARNING: Supabase write failed for {table_name}: {error}")
        return False, error_message
    return True, None


def _first_present(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_bool(value):
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "да"}

    return bool(value)


def _to_int(value):
    if value in (None, ""):
        return None

    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _string_or_none(value):
    if value in (None, ""):
        return None

    return str(value)


def _to_number(value):
    if value in (None, ""):
        return None

    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _json_safe_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _json_safe_row(row):
    return {key: _json_safe_value(value) for key, value in (row or {}).items()}


def _report_date(row):
    value = _first_present(row, ["report_date", "date", "change_date"])

    if isinstance(value, datetime):
        value = value.date().isoformat()
    elif isinstance(value, date):
        value = value.isoformat()
    elif isinstance(value, str) and "—" in value:
        value = value.split("—", maxsplit=1)[0].strip()

    return value or date.today().isoformat()


def get_storage_status():
    return _STORAGE_STATUS.copy()


def _is_table_accessible(table_name):
    try:
        _get_client().table(table_name).select("*").limit(1).execute()
    except Exception as error:
        print(f"WARNING: Supabase healthcheck failed for {table_name}: {error}")
        return False

    return True


def log_storage_configuration():
    print("STORAGE MODE: supabase")
    accessible = str(_is_table_accessible("sellers")).lower()
    print(f"SUPABASE HEALTHCHECK: sellers table accessible: {accessible}")


def get_sellers():
    sellers = _execute_read(
        _get_client().table("sellers").select("*").eq("status", "active"),
        "sellers",
    )

    if not sellers:
        print("WARNING: No active sellers found in Supabase")

    return sellers


def _normalize_product(product):
    normalized_product = product.copy()
    normalized_product.setdefault("seller_id", product.get("seller_id"))
    normalized_product["nmId"] = _first_present(product, ["nmId", "nm_id"])
    normalized_product["vendorCode"] = _first_present(
        product, ["vendorCode", "vendor_code"]
    )
    normalized_product["productName"] = _first_present(
        product, ["productName", "product_name", "title"]
    )
    normalized_product.setdefault("brand", product.get("brand"))
    normalized_product.setdefault("abc", product.get("abc"))
    normalized_product.setdefault("status", product.get("status"))
    return normalized_product


def get_products():
    products = _execute_read(
        _get_client().table("products").select("*").eq("status", "active"),
        "products",
    )
    return [_normalize_product(product) for product in products]


def _extract_card_product(card):
    if not isinstance(card, dict):
        return {}

    product = card.get("product")

    if isinstance(product, dict):
        return {**card, **product}

    return card


def _normalize_wb_card_product(seller_id, card):
    product = _extract_card_product(card)
    nm_id = _to_int(_first_present(product, ["nmID", "nmId", "nm_id"]))

    if nm_id is None:
        return None

    return {
        "seller_id": _to_int(seller_id),
        "nm_id": nm_id,
        "vendor_code": _first_present(product, ["vendorCode", "vendor_code"]),
        "product_name": _first_present(
            product, ["title", "productName", "product_name", "name"]
        ),
        "brand": _first_present(product, ["brand", "brandName"]),
        "abc": "UNKNOWN",
        "status": "active",
    }


def _existing_products_by_nm_id(seller_id, nm_ids):
    if not nm_ids:
        return {}

    products = _execute_read(
        _get_client()
        .table("products")
        .select("seller_id,nm_id,abc,status")
        .eq("seller_id", seller_id)
        .in_("nm_id", nm_ids),
        "products",
    )

    return {product.get("nm_id"): product for product in products}


def sync_products_from_wb_cards(seller_id, cards):
    normalized_by_nm_id = {}

    for card in cards or []:
        product = _normalize_wb_card_product(seller_id, card)

        if product is not None:
            normalized_by_nm_id[product["nm_id"]] = product

    products_to_upsert = list(normalized_by_nm_id.values())
    existing_products = _existing_products_by_nm_id(
        _to_int(seller_id), list(normalized_by_nm_id.keys())
    )

    for product in products_to_upsert:
        existing_product = existing_products.get(product["nm_id"]) or {}
        existing_abc = existing_product.get("abc")
        existing_status = existing_product.get("status")

        if existing_abc and existing_abc != "UNKNOWN":
            product["abc"] = existing_abc

        if existing_status == "inactive":
            product["status"] = existing_status

    print("SUPABASE SYNC PRODUCTS FROM WB:")
    print(f"cards: {len(cards or [])}")
    print(f"upserted: {len(products_to_upsert)}")

    if products_to_upsert:
        _execute_write(
            _get_client()
            .table("products")
            .upsert(products_to_upsert, on_conflict="seller_id,nm_id"),
            "products",
        )


def get_change_log():
    return _execute_read(
        _get_client().table("change_log").select("*"),
        "change_log",
    )


def _normalize_funnel_row(row):
    return {
        "report_date": _report_date(row),
        "seller_id": _to_int(row.get("seller_id")),
        "nm_id": _to_int(_first_present(row, ["nm_id", "nmId", "nmID"])),
        "vendor_code": _first_present(row, ["vendor_code", "vendorCode"]),
        "title": row.get("title"),
        "brand": _first_present(row, ["brand", "brandName"]),
        "open_count": _to_int(_first_present(row, ["open_count", "openCount"])),
        "cart_count": _to_int(_first_present(row, ["cart_count", "cartCount"])),
        "order_count": _to_int(_first_present(row, ["order_count", "orderCount"])),
        "order_sum": _to_number(_first_present(row, ["order_sum", "orderSum"])),
        "add_to_cart_percent": _to_number(
            _first_present(row, ["add_to_cart_percent", "addToCartPercent"])
        ),
        "cart_to_order_percent": _to_number(
            _first_present(row, ["cart_to_order_percent", "cartToOrderPercent"])
        ),
        "wb_stocks": _to_int(_first_present(row, ["wb_stocks", "wbStocks"])),
        "mp_stocks": _to_int(_first_present(row, ["mp_stocks", "mpStocks"])),
        "real_sellable_stock": _to_int(
            _first_present(row, ["real_sellable_stock", "realSellableStock"])
        ),
        "incoming_stock": _to_int(
            _first_present(row, ["incoming_stock", "incomingStock"])
        ),
        "returning_stock": _to_int(
            _first_present(row, ["returning_stock", "returningStock"])
        ),
        "ready_for_sale_stock": _to_int(
            _first_present(row, ["ready_for_sale_stock", "readyForSaleStock"])
        ),
        "acceptance_stock": _to_int(
            _first_present(row, ["acceptance_stock", "acceptanceStock"])
        ),
        "transit_stock": _to_int(
            _first_present(row, ["transit_stock", "transitStock"])
        ),
        "stock_state": _first_present(row, ["stock_state", "stockState"]),
        "raw_json": row,
    }


def get_funnel_history(seller_id, nm_id, days):
    normalized_seller_id = _to_int(seller_id)
    normalized_nm_id = _to_int(nm_id)
    normalized_days = _to_int(days) or 0

    if normalized_seller_id is None or normalized_nm_id is None or normalized_days <= 0:
        return []

    return _execute_read(
        _get_client()
        .table("daily_funnel")
        .select("*")
        .eq("seller_id", normalized_seller_id)
        .eq("nm_id", normalized_nm_id)
        .order("report_date", desc=True)
        .limit(normalized_days),
        "daily_funnel",
    )


def _normalize_problem(problem):
    return {
        "report_date": _report_date(problem),
        "seller_id": _to_int(_first_present(problem, ["seller_id", "sellerId"])),
        "nm_id": _to_int(_first_present(problem, ["nm_id", "nmId", "nmID"])),
        "vendor_code": _first_present(problem, ["vendor_code", "vendorCode"]),
        "title": problem.get("title"),
        "abc": _first_present(problem, ["abc", "ABC"]),
        "problem_type": _first_present(problem, ["problem_type", "problemType"]),
        "problem_label": _first_present(problem, ["problem_label", "problemLabel"]),
        "metric": problem.get("metric"),
        "selected_value": _to_number(
            _first_present(problem, ["selected_value", "selectedValue"])
        ),
        "past_value": _to_number(_first_present(problem, ["past_value", "pastValue"])),
        "baseline_type": _first_present(problem, ["baseline_type", "baselineType"]),
        "baseline_value": _to_number(
            _first_present(problem, ["baseline_value", "baselineValue"])
        ),
        "dynamic_percent": _to_number(
            _first_present(problem, ["dynamic_percent", "dynamicPercent"])
        ),
        "root_cause": _first_present(problem, ["root_cause", "rootCause"]),
        "root_recommendation": _first_present(
            problem, ["root_recommendation", "rootRecommendation"]
        ),
        "severity": _first_present(problem, ["severity"]),
        "severity_score": _to_number(
            _first_present(problem, ["severity_score", "severityScore"])
        ),
        "business_impact_score": _to_number(
            _first_present(
                problem, ["business_impact_score", "businessImpactScore"], default=0
            )
        ),
        "is_below_abc_threshold": _to_bool(
            _first_present(
                problem,
                ["is_below_abc_threshold", "isBelowAbcThreshold"],
                default=False,
            )
        ),
        "lost_orders": _to_number(
            _first_present(problem, ["lost_orders", "lostOrders"])
        ),
        "lost_order_sum": _to_number(
            _first_present(problem, ["lost_order_sum", "lostOrderSum"])
        ),
        "potential_revenue_loss": _to_number(
            _first_present(problem, ["potential_revenue_loss", "potentialRevenueLoss"])
        ),
        "potential_orders_loss": _to_number(
            _first_present(problem, ["potential_orders_loss", "potentialOrdersLoss"])
        ),
        "impact_confidence": _first_present(
            problem, ["impact_confidence", "impactConfidence"]
        ),
        "blocked_revenue_per_day": _to_number(
            _first_present(problem, ["blocked_revenue_per_day", "blockedRevenuePerDay"])
        ),
        "blocked_orders_per_day": _to_number(
            _first_present(problem, ["blocked_orders_per_day", "blockedOrdersPerDay"])
        ),
        "avg_position": _to_number(
            _first_present(problem, ["avg_position", "avgPosition"])
        ),
        "position_delta": _to_number(
            _first_present(problem, ["position_delta", "positionDelta"])
        ),
        "visibility_score": _to_number(
            _first_present(problem, ["visibility_score", "visibilityScore"])
        ),
        "search_visibility_risk": _first_present(
            problem, ["search_visibility_risk", "searchVisibilityRisk"]
        ),
        "recommendation": problem.get("recommendation"),
        "recent_changes": _first_present(problem, ["recent_changes", "recentChanges"]),
        "real_sellable_stock": _to_int(
            _first_present(problem, ["real_sellable_stock", "realSellableStock"])
        ),
        "incoming_stock": _to_int(
            _first_present(problem, ["incoming_stock", "incomingStock"])
        ),
        "returning_stock": _to_int(
            _first_present(problem, ["returning_stock", "returningStock"])
        ),
        "ready_for_sale_stock": _to_int(
            _first_present(problem, ["ready_for_sale_stock", "readyForSaleStock"])
        ),
        "acceptance_stock": _to_int(
            _first_present(problem, ["acceptance_stock", "acceptanceStock"])
        ),
        "transit_stock": _to_int(
            _first_present(problem, ["transit_stock", "transitStock"])
        ),
        "stock_state": _first_present(problem, ["stock_state", "stockState"]),
        "ads_traffic_share": _to_number(
            _first_present(problem, ["ads_traffic_share", "adsTrafficShare"])
        ),
        "low_ads_ctr_flag": _to_bool(
            _first_present(
                problem, ["low_ads_ctr_flag", "lowAdsCTRFlag"], default=False
            )
        ),
        "high_cpc_flag": _to_bool(
            _first_present(problem, ["high_cpc_flag", "highCPCFlag"], default=False)
        ),
        "low_ads_traffic_share_flag": _to_bool(
            _first_present(
                problem,
                ["low_ads_traffic_share_flag", "lowAdsTrafficShareFlag"],
                default=False,
            )
        ),
        "organic_traffic_share": _to_number(
            _first_present(problem, ["organic_traffic_share", "organicTrafficShare"])
        ),
        "ads_orders_share": _to_number(
            _first_present(problem, ["ads_orders_share", "adsOrdersShare"])
        ),
        "organic_orders_share": _to_number(
            _first_present(problem, ["organic_orders_share", "organicOrdersShare"])
        ),
        "decline_source": _first_present(problem, ["decline_source", "declineSource"]),
        "budget_waste_risk": _to_bool(
            _first_present(
                problem, ["budget_waste_risk", "budgetWasteRisk"], default=False
            )
        ),
        "report_trust_score": _first_present(
            problem, ["report_trust_score", "reportTrustScore"]
        ),
        "forecast_eta_hours": _to_number(
            _first_present(problem, ["forecast_eta_hours", "forecastEtaHours"])
        ),
        "days_until_oos": _to_number(
            _first_present(problem, ["days_until_oos", "daysUntilOOS"])
        ),
        "forecast_confidence": _first_present(
            problem, ["forecast_confidence", "forecastConfidence"]
        ),
        "forecast_type": _first_present(problem, ["forecast_type", "forecastType"]),
        "forecast_message": _first_present(
            problem, ["forecast_message", "forecastMessage"]
        ),
    }


def _normalize_task(task):
    return {
        "report_date": _report_date(task),
        "seller_id": _to_int(task.get("seller_id")),
        "nm_id": _to_int(_first_present(task, ["nm_id", "nmId", "nmID"])),
        "vendor_code": _first_present(task, ["vendor_code", "vendorCode"]),
        "title": task.get("title"),
        "problem_type": _first_present(task, ["problem_type", "problemType"]),
        "priority": task.get("priority"),
        "action": task.get("action"),
        "status": task.get("status") or "Новая",
    }


def _drop_empty_required(rows, required_keys):
    return [
        row for row in rows if all(row.get(key) is not None for key in required_keys)
    ]


def save_funnel_snapshot(rows):
    normalized_rows = _drop_empty_required(
        [_normalize_funnel_row(row) for row in rows],
        ["report_date", "nm_id"],
    )
    print(f"SUPABASE SAVE FUNNEL: {len(normalized_rows)} rows")

    if normalized_rows:
        _execute_write(
            _get_client()
            .table("daily_funnel")
            .upsert(normalized_rows, on_conflict="report_date,seller_id,nm_id"),
            "daily_funnel",
        )


def _normalize_ads_metric_row(row):
    return {
        "date": _report_date(row),
        "report_date": _report_date(row),
        "seller_id": _string_or_none(row.get("seller_id")),
        "seller_name": _first_present(row, ["seller_name", "sellerName"]),
        "campaign_id": _to_int(_first_present(row, ["campaign_id", "campaignId"])),
        "campaign_name": _first_present(row, ["campaign_name", "campaignName"]),
        "campaign_status": _first_present(row, ["campaign_status", "campaignStatus"]),
        "campaign_type": _first_present(row, ["campaign_type", "campaignType"]),
        "nm_id": _to_int(_first_present(row, ["nm_id", "nmId", "nm"])),
        "vendor_code": _first_present(row, ["vendor_code", "vendorCode"]),
        "title": row.get("title"),
        "impressions": _to_int(row.get("impressions")) or 0,
        "clicks": _to_int(row.get("clicks")) or 0,
        "ctr": _to_number(row.get("ctr")),
        "cpc": _to_number(row.get("cpc")),
        "cpm": _to_number(row.get("cpm")),
        "spend": _to_number(row.get("spend")) or 0,
        "orders_count": _to_number(
            _first_present(row, ["orders", "orders_count", "ordersCount"])
        )
        or 0,
        "orders": _to_number(
            _first_present(row, ["orders", "orders_count", "ordersCount"])
        )
        or 0,
        "revenue": _to_number(
            _first_present(row, ["revenue", "ordersSum", "orders_sum"])
        )
        or 0,
        "drr": _to_number(row.get("drr")),
        "bid": _to_number(row.get("bid")),
        "bid_delta": _to_number(_first_present(row, ["bid_delta", "bidDelta"])),
        "ctr_delta": _to_number(_first_present(row, ["ctr_delta", "ctrDelta"])),
        "cpc_delta": _to_number(_first_present(row, ["cpc_delta", "cpcDelta"])),
        "drr_delta": _to_number(_first_present(row, ["drr_delta", "drrDelta"])),
        "avg_position": _to_number(
            _first_present(row, ["avg_position", "avgPosition", "avgAdPosition"])
        ),
        "position_delta": _to_number(
            _first_present(row, ["position_delta", "positionDelta"])
        ),
        "ads_root_cause": _first_present(row, ["ads_root_cause", "adsRootCause"]),
        "ads_efficiency_score": _to_number(
            _first_present(row, ["ads_efficiency_score", "adsEfficiencyScore"])
        ),
        "auction_temperature": _first_present(
            row, ["auction_temperature", "auctionTemperature"]
        ),
        "raw_json": row,
    }


def _normalize_ads_campaign_cache_row(seller_id, row):
    return {
        "seller_id": _string_or_none(seller_id),
        "campaign_id": _to_int(
            row.get("campaign_id") or row.get("campaignId") or row.get("advertId")
        ),
        "campaign_name": _first_present(
            row, ["campaign_name", "campaignName", "advertName", "name", "title"]
        ),
        "campaign_status": _first_present(
            row, ["campaign_status", "campaignStatus", "status", "state"]
        ),
        "campaign_type": _first_present(
            row, ["campaign_type", "campaignType", "type", "advertType"]
        ),
        "last_seen_at": datetime.now().isoformat(),
        "raw_json": row.get("raw_json") or row,
    }


def get_ads_campaigns_cache(seller_id):
    rows = _execute_read(
        _get_client()
        .table("ads_campaigns_cache")
        .select("*")
        .eq("seller_id", _string_or_none(seller_id))
        .order("last_stats_at", desc=False, nullsfirst=True),
        "ads_campaigns_cache",
    )
    return rows


def save_ads_campaigns_cache(seller_id, campaigns):
    normalized_rows = _drop_empty_required(
        [_normalize_ads_campaign_cache_row(seller_id, row) for row in campaigns or []],
        ["seller_id", "campaign_id"],
    )
    print(f"SUPABASE SAVE ADS CAMPAIGNS CACHE: {len(normalized_rows)} rows")
    if normalized_rows:
        _execute_write(
            _get_client()
            .table("ads_campaigns_cache")
            .upsert(normalized_rows, on_conflict="seller_id,campaign_id"),
            "ads_campaigns_cache",
        )


def update_ads_campaign_stats_status(
    seller_id, campaign_ids, status, rows=0, error_code=None
):
    normalized_ids = [
        cid
        for cid in (_to_int(value) for value in campaign_ids or [])
        if cid is not None
    ]
    if not normalized_ids:
        return
    should_log_status = len(normalized_ids) > 1 or os.getenv("LOG_LEVEL", "summary").strip().lower() == "debug"
    if should_log_status:
        print(
            "SUPABASE UPDATE ADS CAMPAIGN STATS STATUS: "
            f"{len(normalized_ids)} rows status={status}"
        )
    update_payload = {
        "last_stats_at": datetime.now().isoformat(),
        "last_stats_status": status,
        "last_stats_rows": _to_int(rows) or 0,
        "last_error_code": error_code,
        "consecutive_errors": 0 if status == "success" else 1,
    }
    if status != "success":
        for campaign_id in normalized_ids:
            current = _execute_read(
                _get_client()
                .table("ads_campaigns_cache")
                .select("consecutive_errors")
                .eq("seller_id", _string_or_none(seller_id))
                .eq("campaign_id", campaign_id)
                .limit(1),
                "ads_campaigns_cache",
            )
            previous_errors = (
                _to_int((current or [{}])[0].get("consecutive_errors")) or 0
            )
            update_payload["consecutive_errors"] = previous_errors + 1
            _execute_write(
                _get_client()
                .table("ads_campaigns_cache")
                .update(update_payload)
                .eq("seller_id", _string_or_none(seller_id))
                .eq("campaign_id", campaign_id),
                "ads_campaigns_cache",
            )
        return
    _execute_write(
        _get_client()
        .table("ads_campaigns_cache")
        .update(update_payload)
        .eq("seller_id", _string_or_none(seller_id))
        .in_("campaign_id", normalized_ids),
        "ads_campaigns_cache",
    )


def get_ads_history(seller_id, campaign_id, nm_id=None, days=7, before_date=None):
    query = (
        _get_client()
        .table("daily_ads_metrics")
        .select("*")
        .eq("seller_id", _string_or_none(seller_id))
        .eq("campaign_id", _to_int(campaign_id))
        .order("report_date", desc=True)
        .limit(_to_int(days) or 7)
    )
    if before_date not in (None, ""):
        query = query.lt("report_date", str(before_date))
    normalized_nm_id = _to_int(nm_id)
    if normalized_nm_id is not None:
        query = query.eq("nm_id", normalized_nm_id)
    return _execute_read(query, "daily_ads_metrics")


def get_latest_ads_metrics_by_nm_ids(seller_id, nm_ids):
    rows = []
    seen = set()
    for nm_id in nm_ids or []:
        normalized_nm_id = _to_int(nm_id)
        if normalized_nm_id is None or normalized_nm_id in seen:
            continue
        seen.add(normalized_nm_id)
        latest = _execute_read(
            _get_client()
            .table("daily_ads_metrics")
            .select("*")
            .eq("seller_id", _string_or_none(seller_id))
            .eq("nm_id", normalized_nm_id)
            .order("date", desc=True)
            .limit(1),
            "daily_ads_metrics",
        )
        rows.extend(latest or [])
    return rows


def save_daily_ads_metrics(rows):
    normalized_rows = _drop_empty_required(
        [_normalize_ads_metric_row(row) for row in rows],
        ["report_date", "campaign_id"],
    )
    print(f"SUPABASE SAVE DAILY ADS METRICS: {len(normalized_rows)} rows")

    if normalized_rows:
        _execute_write(
            _get_client()
            .table("daily_ads_metrics")
            .upsert(
                normalized_rows,
                on_conflict="report_date,seller_id,campaign_id,nm_id",
            ),
            "daily_ads_metrics",
        )


def _problem_seller_id(problem):
    return _first_present(problem, ["seller_id", "sellerId"])


def _problem_seller_name(problem):
    return _first_present(problem, ["seller_name", "sellerName"], default="")


def _log_problems_save(problems):
    grouped = {}
    for problem in problems or []:
        if not isinstance(problem, dict):
            continue

        seller_id = _problem_seller_id(problem)
        seller_name = _problem_seller_name(problem)
        if seller_id in (None, ""):
            print("PROBLEM WITHOUT SELLER_ID")

        grouped.setdefault((seller_id, seller_name), 0)
        grouped[(seller_id, seller_name)] += 1

    for (seller_id, seller_name), rows_count in grouped.items():
        print("PROBLEMS SAVE:")
        print(f"seller_id={seller_id}")
        print(f"seller_name={seller_name}")
        print(f"rows={rows_count}")


def save_problems(problems):
    _log_problems_save(problems)
    normalized_problems = [_normalize_problem(problem) for problem in problems]
    print(f"SUPABASE SAVE PROBLEMS: {len(normalized_problems)} rows")

    if normalized_problems:
        _execute_write(
            _get_client().table("problems").insert(normalized_problems),
            "problems",
        )


def _normalize_api_coverage_row(row):
    return {
        "report_date": _report_date(row),
        "seller_name": _first_present(row, ["seller_name", "sellerName"]),
        "nm_id": _to_int(_first_present(row, ["nm_id", "nmId", "nmID"])),
        "vendor_code": _first_present(row, ["vendor_code", "vendorCode"]),
        "title": row.get("title"),
        "in_cards_api": _to_bool(
            _first_present(row, ["in_cards_api", "inCardsApi"], default=False)
        ),
        "in_products_catalog": _to_bool(
            _first_present(
                row, ["in_products_catalog", "inProductsCatalog"], default=False
            )
        ),
        "in_funnel_api": _to_bool(
            _first_present(row, ["in_funnel_api", "inFunnelApi"], default=False)
        ),
        "in_ads_api": _to_bool(
            _first_present(row, ["in_ads_api", "inAdsApi"], default=False)
        ),
        "in_supplies_api": _to_bool(
            _first_present(row, ["in_supplies_api", "inSuppliesApi"], default=False)
        ),
        "in_problems": _to_bool(
            _first_present(row, ["in_problems", "inProblems"], default=False)
        ),
        "in_telegram_top": _to_bool(
            _first_present(row, ["in_telegram_top", "inTelegramTop"], default=False)
        ),
        "has_funnel_metrics": _to_bool(
            _first_present(
                row, ["has_funnel_metrics", "hasFunnelMetrics"], default=False
            )
        ),
        "has_ads_metrics": _to_bool(
            _first_present(row, ["has_ads_metrics", "hasAdsMetrics"], default=False)
        ),
        "has_supply_metrics": _to_bool(
            _first_present(
                row, ["has_supply_metrics", "hasSupplyMetrics"], default=False
            )
        ),
        "has_forecast": _to_bool(
            _first_present(row, ["has_forecast", "hasForecast"], default=False)
        ),
        "has_business_impact": _to_bool(
            _first_present(
                row, ["has_business_impact", "hasBusinessImpact"], default=False
            )
        ),
        "funnel_fields_filled": _to_int(
            _first_present(row, ["funnel_fields_filled", "funnelFieldsFilled"])
        ),
        "ads_fields_filled": _to_int(
            _first_present(row, ["ads_fields_filled", "adsFieldsFilled"])
        ),
        "supply_fields_filled": _to_int(
            _first_present(row, ["supply_fields_filled", "supplyFieldsFilled"])
        ),
        "problem_count": _to_int(
            _first_present(row, ["problem_count", "problemCount"])
        ),
        "ads_problem_count": _to_int(
            _first_present(row, ["ads_problem_count", "adsProblemCount"])
        ),
        "funnel_problem_count": _to_int(
            _first_present(row, ["funnel_problem_count", "funnelProblemCount"])
        ),
        "ads_campaign_count": _to_int(
            _first_present(row, ["ads_campaign_count", "adsCampaignCount"])
        ),
        "ads_rows_count": _to_int(
            _first_present(row, ["ads_rows_count", "adsRowsCount"])
        ),
    }


def save_api_coverage_daily(rows):
    normalized_rows = _drop_empty_required(
        [_normalize_api_coverage_row(row) for row in rows],
        ["report_date", "seller_name", "nm_id"],
    )
    print(f"SUPABASE SAVE API COVERAGE DAILY: {len(normalized_rows)} rows")

    if normalized_rows:
        _execute_write(
            _get_client()
            .table("api_coverage_daily")
            .upsert(normalized_rows, on_conflict="report_date,seller_name,nm_id"),
            "api_coverage_daily",
        )


def create_tasks(tasks):
    normalized_tasks = _drop_empty_required(
        [_normalize_task(task) for task in tasks],
        ["report_date", "nm_id", "problem_type"],
    )
    print(f"SUPABASE CREATE TASKS: {len(normalized_tasks)} rows")

    if normalized_tasks:
        _execute_write(
            _get_client()
            .table("tasks")
            .upsert(
                normalized_tasks, on_conflict="report_date,seller_id,nm_id,problem_type"
            ),
            "tasks",
        )


def _normalize_qbiki_metric_row(row):
    return {
        "date": _report_date(row),
        "seller_name": _first_present(row, ["seller_name", "sellerName"]),
        "nm_id": _to_int(_first_present(row, ["nm_id", "nmId", "nmID"])),
        "vendor_code": _first_present(row, ["vendor_code", "vendorCode"]),
        "title": row.get("title"),
        "orders_per_1000_impressions": _to_number(row.get("ordersPer1000Impressions")),
        "organic_cr": _to_number(row.get("organicCR")),
        "ads_cr": _to_number(row.get("adsCR")),
        "ads_orders": _to_number(row.get("adsOrders")),
        "ads_impressions": _to_number(row.get("adsImpressions")),
        "ads_ctr": _to_number(row.get("adsCTR")),
        "ads_clicks": _to_number(row.get("adsClicks")),
        "cart_conversion": _to_number(row.get("cartConversion")),
        "order_conversion": _to_number(row.get("orderConversion")),
        "avg_ad_bid": _to_number(row.get("avgAdBid")),
        "ad_profit_per_order": _to_number(row.get("adProfitPerOrder")),
        "cpo": _to_number(row.get("CPO")),
        "drr": _to_number(row.get("DRR")),
        "clean_drr": _to_number(row.get("cleanDRR")),
        "clean_margin": _to_number(row.get("cleanMargin")),
        "clean_margin_organic": _to_number(row.get("cleanMarginOrganic")),
        "clean_margin_ads": _to_number(row.get("cleanMarginAds")),
        "roi": _to_number(row.get("ROI")),
        "wb_stock": _to_number(row.get("wbStock")),
        "days_of_stock": _to_number(row.get("daysOfStock")),
    }


def save_daily_qbiki_metrics(rows):
    normalized_rows = _drop_empty_required(
        [_normalize_qbiki_metric_row(row) for row in rows],
        ["date", "nm_id"],
    )
    print(f"SUPABASE SAVE DAILY QBIKI METRICS: {len(normalized_rows)} rows")

    if normalized_rows:
        _execute_write(
            _get_client().table("daily_qbiki_metrics").insert(normalized_rows),
            "daily_qbiki_metrics",
        )



def _to_nullable_number(value):
    if value in (None, ""):
        return None
    return _to_number(value)


def _normalize_ads_bid_history_row(row, seller_id=None, seller_name=None):
    return _json_safe_row(
        {
            "seller_id": _to_int(
                seller_id
                if seller_id not in (None, "")
                else _first_present(row, ["seller_id", "sellerId"])
            ),
            "seller_name": (
                seller_name
                if seller_name not in (None, "")
                else _first_present(row, ["seller_name", "sellerName"])
            ),
            "campaign_id": _to_int(
                _first_present(row, ["campaign_id", "campaignId", "id"])
            ),
            "nm_id": _to_int(_first_present(row, ["nm_id", "nmId"])),
            "report_date": _report_date(row),
            "bid_type": _first_present(row, ["bid_type", "bidType"]),
            "payment_type": _first_present(row, ["payment_type", "paymentType"]),
            "search_bid": _to_nullable_number(
                _first_present(row, ["search_bid", "searchBid"])
            ),
            "recommendations_bid": _to_nullable_number(
                _first_present(row, ["recommendations_bid", "recommendationsBid"])
            ),
            "campaign_status": _to_int(
                _first_present(row, ["campaign_status", "campaignStatus", "status"])
            ),
            "campaign_updated_at": _first_present(
                row, ["campaign_updated_at", "campaignUpdatedAt"]
            ),
        }
    )


def save_ads_bid_history(rows, seller_id=None, seller_name=None):
    normalized_rows = _drop_empty_required(
        [
            _normalize_ads_bid_history_row(
                row, seller_id=seller_id, seller_name=seller_name
            )
            for row in rows or []
        ],
        ["campaign_id", "report_date"],
    )
    print(f"SUPABASE SAVE ADS BID HISTORY: {len(normalized_rows)} rows")
    success = True
    error_message = None
    if normalized_rows:
        success, error_message = _execute_write(
            _get_client()
            .table("ads_bid_history")
            .upsert(normalized_rows, on_conflict="campaign_id,nm_id,report_date"),
            "ads_bid_history",
        )
    print(
        "ADS BID HISTORY SAVED: "
        f"rows={len(normalized_rows) if success else 0} "
        f"seller_id={seller_id} seller_name={seller_name}"
    )
    if success:
        return len(normalized_rows)
    print(f"WARNING: ADS BID HISTORY SAVE FAILED: {error_message}")
    return 0


def _bid_history_key(row):
    return (
        _to_int(_first_present(row, ["campaign_id", "campaignId"])),
        _to_int(_first_present(row, ["nm_id", "nmId"])),
    )


def enrich_ads_bid_history_changes(rows, seller_id=None):
    enriched_rows = []
    for row in rows or []:
        enriched = dict(row)
        normalized = _normalize_ads_bid_history_row(row, seller_id=seller_id)
        campaign_id, nm_id = _bid_history_key(normalized)
        report_date = normalized.get("report_date")
        if campaign_id is None or report_date in (None, ""):
            enriched_rows.append(enriched)
            continue
        query = (
            _get_client()
            .table("ads_bid_history")
            .select("*")
            .eq("campaign_id", campaign_id)
            .lt("report_date", report_date)
            .order("report_date", desc=True)
            .limit(1)
        )
        if seller_id not in (None, ""):
            query = query.eq("seller_id", _to_int(seller_id))
        if nm_id is None:
            query = query.is_("nm_id", "null")
        else:
            query = query.eq("nm_id", nm_id)
        previous_rows = _execute_read(query, "ads_bid_history")
        previous = (previous_rows or [{}])[0]
        has_previous_history = bool(previous_rows)
        previous_search = previous.get("search_bid")
        previous_recommendations = previous.get("recommendations_bid")
        current_search = normalized.get("search_bid")
        current_recommendations = normalized.get("recommendations_bid")
        enriched["has_previous_bid_history"] = has_previous_history
        enriched["previous_search_bid"] = None
        enriched["previous_recommendations_bid"] = None
        if previous_search not in (None, "") and current_search not in (None, ""):
            enriched["previous_search_bid"] = previous_search
            enriched["search_bid_delta"] = _to_number(current_search) - _to_number(
                previous_search
            )
        if (
            previous_recommendations not in (None, "")
            and current_recommendations not in (None, "")
        ):
            enriched["previous_recommendations_bid"] = previous_recommendations
            enriched["recommendations_bid_delta"] = _to_number(
                current_recommendations
            ) - _to_number(previous_recommendations)
        enriched_rows.append(enriched)
    return enriched_rows


def get_ads_bid_history_unique_dates_count(nm_ids=None):
    query = _get_client().table("ads_bid_history").select("report_date,nm_id")
    normalized_nm_ids = [
        _to_int(nm_id)
        for nm_id in nm_ids or []
        if _to_int(nm_id) is not None
    ]
    if normalized_nm_ids:
        query = query.in_("nm_id", list(set(normalized_nm_ids)))
    rows = _execute_read(query, "ads_bid_history")
    return len(
        {row.get("report_date") for row in rows or [] if row.get("report_date") not in (None, "")}
    )


def get_latest_ads_bid_history_by_nm_ids(nm_ids, report_date=None):
    rows = []
    seen = set()
    for nm_id in nm_ids or []:
        normalized_nm_id = _to_int(nm_id)
        if normalized_nm_id is None or normalized_nm_id in seen:
            continue
        seen.add(normalized_nm_id)
        query = (
            _get_client()
            .table("ads_bid_history")
            .select("*")
            .eq("nm_id", normalized_nm_id)
        )
        if report_date not in (None, ""):
            query = query.eq("report_date", _json_safe_value(report_date))
        latest_rows = _execute_read(
            query.order("report_date", desc=True), "ads_bid_history"
        )
        rows.extend(
            enrich_ads_bid_history_changes(latest_rows, seller_id=None) or []
        )
    return rows
