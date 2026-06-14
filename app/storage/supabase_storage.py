import os
from datetime import date
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
        print(f"WARNING: Supabase write failed for {table_name}: {error}")


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


def _to_number(value):
    if value in (None, ""):
        return None

    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _report_date(row):
    value = _first_present(row, ["report_date", "date", "change_date"])

    if isinstance(value, str) and "—" in value:
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
        "seller_id": _to_int(problem.get("seller_id")),
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


def save_problems(problems):
    normalized_problems = [_normalize_problem(problem) for problem in problems]
    print(f"SUPABASE SAVE PROBLEMS: {len(normalized_problems)} rows")

    if normalized_problems:
        _execute_write(
            _get_client().table("problems").insert(normalized_problems),
            "problems",
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
