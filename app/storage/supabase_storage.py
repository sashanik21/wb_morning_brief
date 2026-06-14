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
        "raw_json": row,
    }


def _normalize_problem(problem):
    return {
        "report_date": _report_date(problem),
        "seller_id": _to_int(problem.get("seller_id")),
        "nm_id": _to_int(_first_present(problem, ["nm_id", "nmId", "nmID"])),
        "vendor_code": _first_present(problem, ["vendor_code", "vendorCode"]),
        "title": problem.get("title"),
        "abc": problem.get("abc"),
        "problem_type": _first_present(problem, ["problem_type", "problemType"]),
        "problem_label": _first_present(problem, ["problem_label", "problemLabel"]),
        "metric": problem.get("metric"),
        "selected_value": _to_number(
            _first_present(problem, ["selected_value", "selectedValue"])
        ),
        "past_value": _to_number(_first_present(problem, ["past_value", "pastValue"])),
        "dynamic_percent": _to_number(
            _first_present(problem, ["dynamic_percent", "dynamicPercent"])
        ),
        "root_cause": _first_present(problem, ["root_cause", "rootCause"]),
        "root_recommendation": _first_present(
            problem, ["root_recommendation", "rootRecommendation"]
        ),
        "severity_score": _to_number(
            _first_present(problem, ["severity_score", "severityScore"])
        ),
        "recommendation": problem.get("recommendation"),
        "recent_changes": _first_present(problem, ["recent_changes", "recentChanges"]),
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
