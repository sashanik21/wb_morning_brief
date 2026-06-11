from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.collectors.cards import get_cards_list
from app.config import ABC_RULES, HEADERS
from app.seller_config import SELLER_NAME
from app.sheets.google_sheets import get_change_log, get_products
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
    "wbStocks",
    "mpStocks",
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
    "selectedValue",
    "pastValue",
    "dynamicPercent",
    "recommendation",
    "recentChanges",
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


def _build_problem_row(
    record,
    rule,
    selected_value,
    past_value,
    dynamic_percent,
    products_by_nm_id,
    recent_changes,
):
    return {
        "sellerName": SELLER_NAME,
        "nmId": _problem_product_value(record, "nmId"),
        "vendorCode": _problem_product_value(record, "vendorCode"),
        "brandName": _problem_product_value(record, "brandName"),
        "title": _problem_product_value(record, "title"),
        "ABC": _product_abc(record, products_by_nm_id),
        "productInCatalog": _product_in_catalog(record, products_by_nm_id),
        "productStatus": _product_status(record, products_by_nm_id),
        "problemType": rule["problem_type"],
        "metric": rule["metric"],
        "selectedValue": _format_problem_number(selected_value),
        "pastValue": _format_problem_number(past_value),
        "dynamicPercent": round(dynamic_percent, 2),
        "recommendation": _recommendation(
            record, products_by_nm_id, rule["recommendation"]
        ),
        "recentChanges": recent_changes,
    }


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


def _build_record_problem_rows(record, products_by_nm_id, recent_changes=""):
    record_problem_rows = []

    for rule in PROBLEM_RULES:
        selected_value, past_value, dynamic_percent = _metric_dynamic_percent(
            record, rule
        )

        if dynamic_percent is None or dynamic_percent > rule["threshold"]:
            continue

        record_problem_rows.append(
            _build_problem_row(
                record,
                rule,
                selected_value,
                past_value,
                dynamic_percent,
                products_by_nm_id,
                recent_changes,
            )
        )

    wb_stocks = _to_number(_problem_product_value(record, "wbStocks"))

    if wb_stocks == 0:
        record_problem_rows.append(
            {
                "sellerName": SELLER_NAME,
                "nmId": _problem_product_value(record, "nmId"),
                "vendorCode": _problem_product_value(record, "vendorCode"),
                "brandName": _problem_product_value(record, "brandName"),
                "title": _problem_product_value(record, "title"),
                "ABC": _product_abc(record, products_by_nm_id),
                "productInCatalog": _product_in_catalog(record, products_by_nm_id),
                "productStatus": _product_status(record, products_by_nm_id),
                "problemType": "wbStocks == 0",
                "metric": "wbStocks",
                "selectedValue": 0,
                "pastValue": "",
                "dynamicPercent": "",
                "recommendation": _recommendation(
                    record, products_by_nm_id, STOCK_PROBLEM_RECOMMENDATION
                ),
                "recentChanges": recent_changes,
            }
        )

    return record_problem_rows


def count_sku_ignored_by_abc_filter(funnel_data):
    ignored_sku_count = 0
    products_by_nm_id = _build_products_by_nm_id()

    for record in _extract_problem_records(funnel_data):
        if _build_record_problem_rows(
            record, products_by_nm_id
        ) and not _passes_abc_filter(record, products_by_nm_id):
            ignored_sku_count += 1

    return ignored_sku_count


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


def analyze_funnel_problems(funnel_data):
    problem_rows = []
    ignored_sku_count = 0
    products_by_nm_id = _build_products_by_nm_id()
    recent_changes_by_nm_id = _build_recent_changes_by_nm_id()

    for record in _extract_problem_records(funnel_data):
        recent_changes = _recent_changes(record, recent_changes_by_nm_id)
        record_problem_rows = _build_record_problem_rows(
            record, products_by_nm_id, recent_changes
        )

        if not record_problem_rows:
            continue

        if not _passes_abc_filter(record, products_by_nm_id):
            ignored_sku_count += 1
            continue

        problem_rows.extend(record_problem_rows)

    print("ABC FILTER:")
    print(f"ignored SKU: {ignored_sku_count}")
    print(f"remaining problems: {len(problem_rows)}")

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
            f"{row['metric']}: {row['selectedValue']} vs {row['pastValue']} "
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
                "sellerName": SELLER_NAME,
                "date": report_date,
                "nmId": _first_present(record, ["product.nmId", "nmId", "nmID"]),
                "vendorCode": _first_present(
                    record, ["product.vendorCode", "vendorCode"]
                ),
                "brandName": _first_present(record, ["product.brandName", "brandName"]),
                "title": _first_present(record, ["product.title", "title"]),
                "openCount": _first_present(
                    record,
                    ["statistic.selected.openCount", "selected.openCount", "openCount"],
                ),
                "cartCount": _first_present(
                    record,
                    ["statistic.selected.cartCount", "selected.cartCount", "cartCount"],
                ),
                "orderCount": _first_present(
                    record,
                    [
                        "statistic.selected.orderCount",
                        "selected.orderCount",
                        "orderCount",
                    ],
                ),
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
                "wbStocks": _first_present(record, ["product.stocks.wb", "stocks.wb"]),
                "mpStocks": _first_present(record, ["product.stocks.mp", "stocks.mp"]),
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


def save_funnel_problems_report(funnel_data):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    report_date = datetime.now().date().strftime("%Y_%m_%d")
    report_path = REPORTS_DIR / f"problems_{report_date}.xlsx"
    dataframe = analyze_funnel_problems(funnel_data)

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
