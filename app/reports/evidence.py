import html
from datetime import datetime

import pandas as pd

from app.seller_config import SELLER_NAME

EVIDENCE_LIMIT_TELEGRAM = 5
EVIDENCE_LIMIT_DASHBOARD = 10
EVIDENCE_METRICS = {
    "openCount": "Переходы",
    "cartCount": "Корзины",
    "orderCount": "Заказы",
    "orderSum": "Выручка",
    "addToCartPercent": "Конверсия в корзину",
    "cartToOrderPercent": "Конверсия в заказ",
}


def _is_missing(value):
    if value is None or value == "":
        return True

    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _to_number(value):
    if _is_missing(value):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        normalized = value.replace("%", "").replace(" ", "").replace(",", ".")

        try:
            return float(normalized)
        except ValueError:
            return None

    return None


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
        for path in ("data.products", "products", "data"):
            products = _get_nested_value(funnel_data, path)

            if isinstance(products, list):
                return products

    if isinstance(funnel_data, list):
        return funnel_data

    if isinstance(funnel_data, pd.DataFrame):
        return funnel_data.to_dict("records")

    return []


def _flatten_record(record):
    if not isinstance(record, dict):
        return {}

    return pd.json_normalize(record, sep=".").to_dict("records")[0]


def _metric_paths(period, metric):
    period_aliases = {
        "selected": ["selected", "selectedPeriod"],
        "past": ["past", "pastPeriod"],
    }[period]
    paths = []

    for period_alias in period_aliases:
        if metric in {"addToCartPercent", "cartToOrderPercent"}:
            paths.extend(
                [
                    f"statistic.{period_alias}.conversions.{metric}",
                    f"statistics.{period_alias}.conversions.{metric}",
                    f"{period_alias}.conversions.{metric}",
                ]
            )

            if metric == "addToCartPercent":
                paths.extend(
                    [
                        f"statistic.{period_alias}.addToCartConversion",
                        f"statistics.{period_alias}.addToCartConversion",
                        f"{period_alias}.addToCartConversion",
                        f"statistic.{period_alias}.addToCartPercent",
                        f"statistics.{period_alias}.addToCartPercent",
                        f"{period_alias}.addToCartPercent",
                    ]
                )
            else:
                paths.extend(
                    [
                        f"statistic.{period_alias}.cartToOrderConversion",
                        f"statistics.{period_alias}.cartToOrderConversion",
                        f"{period_alias}.cartToOrderConversion",
                        f"statistic.{period_alias}.cartToOrderPercent",
                        f"statistics.{period_alias}.cartToOrderPercent",
                        f"{period_alias}.cartToOrderPercent",
                    ]
                )

        paths.extend(
            [
                f"statistic.{period_alias}.{metric}",
                f"statistics.{period_alias}.{metric}",
                f"{period_alias}.{metric}",
                f"{metric}.{period_alias}",
            ]
        )

    if period == "selected":
        paths.append(metric)

    return paths


def _dynamic_percent(current_value, past_value):
    current_number = _to_number(current_value)
    past_number = _to_number(past_value)

    if current_number is None or past_number in (None, 0):
        return None

    return ((current_number - past_number) / past_number) * 100


def _fallback_conversion(row, period, numerator_metric, denominator_metric):
    numerator = _to_number(row.get(f"{numerator_metric}_{period}"))
    denominator = _to_number(row.get(f"{denominator_metric}_{period}"))

    if numerator is None or denominator in (None, 0):
        return None

    return numerator / denominator * 100


def _diagnosis(row):
    orders_delta = row.get("orderCount_delta")
    opens_delta = row.get("openCount_delta")
    carts_delta = row.get("cartCount_delta")
    order_sum_delta = row.get("orderSum_delta")
    cart_to_order_delta = row.get("cartToOrderPercent_delta")
    wb_stocks = _to_number(row.get("wbStocks"))

    orders_fell = orders_delta is not None and orders_delta < 0
    opens_fell = opens_delta is not None and opens_delta < 0
    carts_fell = carts_delta is not None and carts_delta < 0
    carts_stable = carts_delta is not None and carts_delta >= 0
    opens_stable = opens_delta is not None and opens_delta >= 0
    conversion_order_fell = cart_to_order_delta is not None and cart_to_order_delta < 0
    revenue_fell = order_sum_delta is not None and order_sum_delta < 0

    if wb_stocks == 0:
        return "Проблема остатков"

    if orders_fell and opens_fell:
        return "Просадка трафика"

    if orders_fell and opens_stable and carts_fell:
        return "Проблема карточки/цены"

    if orders_fell and carts_stable and conversion_order_fell:
        return "Проблема доставки/остатков/цены"

    if revenue_fell and not orders_fell:
        return "Проблема среднего чека"

    return "Требует проверки"


def _absolute_drop(row, metric):
    selected_value = _to_number(row.get(f"{metric}_selected")) or 0
    past_value = _to_number(row.get(f"{metric}_past")) or 0

    return max(past_value - selected_value, 0)


def _sort_key(row):
    return (_absolute_drop(row, "orderCount"), _absolute_drop(row, "orderSum"))


def build_evidence_rows(funnel_data, limit=None):
    rows = []

    for source_record in _extract_products(funnel_data):
        record = _flatten_record(source_record)
        row = {
            "sellerName": _first_present(record, ["sellerName"], SELLER_NAME),
            "date": _first_present(
                record,
                [
                    "statistic.selected.period.start",
                    "statistic.selectedPeriod.period.start",
                    "statistic.selectedPeriod.start",
                    "selected.period.start",
                    "selectedPeriod.period.start",
                    "selectedPeriod.start",
                    "period.start",
                    "date",
                ],
                default=datetime.now().strftime("%Y-%m-%d"),
            ),
            "title": _first_present(
                record, ["product.title", "title"], default="Без названия"
            ),
            "vendorCode": _first_present(
                record, ["product.vendorCode", "vendorCode"], default="n/a"
            ),
            "nmId": _first_present(record, ["product.nmId", "nmId", "nmID"], "n/a"),
            "wbStocks": _first_present(record, ["product.stocks.wb", "stocks.wb"]),
        }

        for metric in EVIDENCE_METRICS:
            selected_value = _first_present(record, _metric_paths("selected", metric))
            past_value = _first_present(record, _metric_paths("past", metric))
            row[f"{metric}_selected"] = _to_number(selected_value)
            row[f"{metric}_past"] = _to_number(past_value)
            row[f"{metric}_delta"] = _dynamic_percent(selected_value, past_value)

        if row["addToCartPercent_selected"] is None:
            row["addToCartPercent_selected"] = _fallback_conversion(
                row, "selected", "cartCount", "openCount"
            )
        if row["addToCartPercent_past"] is None:
            row["addToCartPercent_past"] = _fallback_conversion(
                row, "past", "cartCount", "openCount"
            )
        row["addToCartPercent_delta"] = _dynamic_percent(
            row["addToCartPercent_selected"], row["addToCartPercent_past"]
        )

        if row["cartToOrderPercent_selected"] is None:
            row["cartToOrderPercent_selected"] = _fallback_conversion(
                row, "selected", "orderCount", "cartCount"
            )
        if row["cartToOrderPercent_past"] is None:
            row["cartToOrderPercent_past"] = _fallback_conversion(
                row, "past", "orderCount", "cartCount"
            )
        row["cartToOrderPercent_delta"] = _dynamic_percent(
            row["cartToOrderPercent_selected"], row["cartToOrderPercent_past"]
        )
        row["diagnosis"] = _diagnosis(row)

        if _sort_key(row) != (0, 0):
            rows.append(row)

    rows = sorted(rows, key=_sort_key, reverse=True)

    if limit is not None:
        return rows[:limit]

    return rows


def format_number(value):
    number = _to_number(value)

    if number is None:
        return "n/a"

    if float(number).is_integer():
        return f"{int(number):,}".replace(",", " ")

    return f"{number:,.1f}".replace(",", " ").replace(".", ",")


def format_percent(value):
    number = _to_number(value)

    if number is None:
        return "n/a"

    return f"{number:.0f}%" if float(number).is_integer() else f"{number:.1f}%"


def escape(value):
    return html.escape(str(value))
