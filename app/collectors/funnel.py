from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from app.collectors.cards import get_cards_list
from app.config import HEADERS
from app.wb_client import WBClient

SALES_FUNNEL_URL = (
    "https://seller-analytics-api.wildberries.ru"
    "/api/analytics/v3/sales-funnel/products"
)
MAX_FUNNEL_NM_IDS = 1000
REPORTS_DIR = Path("reports")
FUNNEL_REPORT_COLUMNS = [
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

        if value is not None:
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
