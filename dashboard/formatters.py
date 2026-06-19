"""Formatting and normalization helpers for dashboard data."""

from collections import Counter

import pandas as pd


MONEY_COLUMNS = ["lost_revenue"]
INTEGER_COLUMNS = ["lost_orders", "critical_sku"]


def to_number(value, default=0):
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def first_present(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def reason_value(row):
    return first_present(
        row,
        ["root_cause", "problem_label", "problem_type", "decline_source", "metric"],
        "Не определено",
    )


def seller_name(row, sellers_by_id):
    seller_id = first_present(row, ["seller_id", "sellerId"])
    return sellers_by_id.get(str(seller_id), f"seller_id={seller_id}" if seller_id else "Без seller_id")


def lost_revenue(row):
    return to_number(
        first_present(
            row,
            [
                "potential_revenue_loss",
                "potentialRevenueLoss",
                "lost_order_sum",
                "lostOrderSum",
                "blocked_revenue_per_day",
                "blockedRevenuePerDay",
            ],
        )
    )


def lost_orders(row):
    return to_number(
        first_present(
            row,
            [
                "potential_orders_loss",
                "potentialOrdersLoss",
                "lost_orders",
                "lostOrders",
                "blocked_orders_per_day",
                "blockedOrdersPerDay",
            ],
        )
    )


def severity_status(rows):
    severities = {str(row.get("severity") or "").lower() for row in rows}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "watch"


def main_reason(rows):
    reasons = [reason_value(row) for row in rows]
    return Counter(reasons).most_common(1)[0][0] if reasons else "Не определено"


def format_money(value):
    return f"{to_number(value):,.0f} ₽".replace(",", " ")


def format_number(value):
    return f"{to_number(value):,.0f}".replace(",", " ")


def prepare_seller_table(problems, sellers_by_id):
    grouped = {}
    for row in problems:
        seller_id = str(first_present(row, ["seller_id", "sellerId"], ""))
        grouped.setdefault(seller_id, []).append(row)

    records = []
    for seller_id, rows in grouped.items():
        records.append(
            {
                "продавец": sellers_by_id.get(seller_id, f"seller_id={seller_id}" if seller_id else "Без seller_id"),
                "потеря выручки": sum(lost_revenue(row) for row in rows),
                "потеря заказов": sum(lost_orders(row) for row in rows),
                "критичных SKU": len({first_present(row, ["nm_id", "nmId"]) for row in rows if first_present(row, ["nm_id", "nmId"])}),
                "главная причина": main_reason(rows),
                "статус": severity_status(rows),
            }
        )

    return pd.DataFrame(records).sort_values("потеря выручки", ascending=False) if records else pd.DataFrame()


def prepare_sku_table(problems, sellers_by_id):
    records = []
    for row in problems:
        records.append(
            {
                "продавец": seller_name(row, sellers_by_id),
                "артикул WB": first_present(row, ["nm_id", "nmId", "nmID"], ""),
                "название": first_present(row, ["title", "productName", "product_name"], ""),
                "потеря выручки": lost_revenue(row),
                "потеря заказов": lost_orders(row),
                "причина": reason_value(row),
                "подтверждение": first_present(row, ["impact_confidence", "report_trust_score", "reportTrustScore"], ""),
                "действие": first_present(row, ["root_recommendation", "recommendation", "forecast_message"], ""),
            }
        )

    return pd.DataFrame(records).sort_values("потеря выручки", ascending=False) if records else pd.DataFrame()
