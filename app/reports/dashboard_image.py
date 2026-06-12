from datetime import datetime
from pathlib import Path

import matplotlib
import pandas as pd

from app.reports.evidence import (
    EVIDENCE_LIMIT_DASHBOARD,
    build_evidence_rows,
    format_number,
    format_percent,
)
from app.seller_config import SELLER_NAME

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPORT_COLUMNS = [
    "Товар",
    "Артикул продавца",
    "Артикул WB",
    "Переходы сейчас",
    "Переходы было",
    "Δ переходы %",
    "Корзины сейчас",
    "Корзины было",
    "Δ корзины %",
    "Заказы сейчас",
    "Заказы было",
    "Δ заказы %",
    "Выручка сейчас",
    "Выручка было",
    "Δ выручка %",
    "Конверсия в корзину сейчас",
    "Конверсия в корзину было",
    "Δ конверсия корзина %",
    "Конверсия в заказ сейчас",
    "Конверсия в заказ было",
    "Δ конверсия заказ %",
    "Вывод",
]
DELTA_COLUMN_METRICS = {
    "Δ переходы %": "openCount_delta",
    "Δ корзины %": "cartCount_delta",
    "Δ заказы %": "orderCount_delta",
    "Δ выручка %": "orderSum_delta",
    "Δ конверсия корзина %": "addToCartPercent_delta",
    "Δ конверсия заказ %": "cartToOrderPercent_delta",
}
MONEY_METRICS = {"orderSum_selected", "orderSum_past"}
PERCENT_METRICS = {
    "addToCartPercent_selected",
    "addToCartPercent_past",
    "cartToOrderPercent_selected",
    "cartToOrderPercent_past",
}
HEADER_COLOR = "#E5E7EB"
NEGATIVE_COLOR = "#FEE2E2"
NEUTRAL_COLOR = "#FEF3C7"
POSITIVE_COLOR = "#DCFCE7"
TOTAL_ROW_COLOR = "#DBEAFE"
ROW_COLOR = "#FFFFFF"
GRID_COLOR = "#CBD5E1"
TITLE_COLOR = "#111827"
TEXT_COLOR = "#1F2937"
BACKGROUND_COLOR = "#FFFFFF"
EMPTY_VALUE = ""


def _format_metric(value, metric):
    if metric in PERCENT_METRICS:
        return format_percent(value)

    formatted = format_number(value)

    if metric in MONEY_METRICS and formatted != "n/a":
        return f"{formatted} ₽"

    return formatted


def _format_delta(value):
    return format_percent(value)


def _table_row(row):
    return [
        str(row.get("title") or "Без названия"),
        str(row.get("vendorCode") or "n/a"),
        str(row.get("nmId") or "n/a"),
        _format_metric(row.get("openCount_selected"), "openCount_selected"),
        _format_metric(row.get("openCount_past"), "openCount_past"),
        _format_delta(row.get("openCount_delta")),
        _format_metric(row.get("cartCount_selected"), "cartCount_selected"),
        _format_metric(row.get("cartCount_past"), "cartCount_past"),
        _format_delta(row.get("cartCount_delta")),
        _format_metric(row.get("orderCount_selected"), "orderCount_selected"),
        _format_metric(row.get("orderCount_past"), "orderCount_past"),
        _format_delta(row.get("orderCount_delta")),
        _format_metric(row.get("orderSum_selected"), "orderSum_selected"),
        _format_metric(row.get("orderSum_past"), "orderSum_past"),
        _format_delta(row.get("orderSum_delta")),
        _format_metric(
            row.get("addToCartPercent_selected"), "addToCartPercent_selected"
        ),
        _format_metric(row.get("addToCartPercent_past"), "addToCartPercent_past"),
        _format_delta(row.get("addToCartPercent_delta")),
        _format_metric(
            row.get("cartToOrderPercent_selected"), "cartToOrderPercent_selected"
        ),
        _format_metric(row.get("cartToOrderPercent_past"), "cartToOrderPercent_past"),
        _format_delta(row.get("cartToOrderPercent_delta")),
        str(row.get("diagnosis") or "Требует проверки"),
    ]


def _sum_metric(rows, metric):
    values = [row.get(metric) for row in rows]
    numbers = pd.to_numeric(pd.Series(values), errors="coerce")

    if numbers.notna().any():
        return numbers.sum()

    return None


def _dynamic(current_value, past_value):
    if current_value is None or past_value in (None, 0):
        return None

    return ((current_value - past_value) / past_value) * 100


def _weighted_percent(rows, value_metric, weight_metric):
    total_weight = _sum_metric(rows, weight_metric)

    if total_weight in (None, 0):
        return None

    total = 0

    for row in rows:
        value = row.get(value_metric)
        weight = row.get(weight_metric)

        if value is None or weight in (None, 0):
            continue

        total += value * weight

    return total / total_weight


def _total_row(rows):
    open_selected = _sum_metric(rows, "openCount_selected")
    open_past = _sum_metric(rows, "openCount_past")
    cart_selected = _sum_metric(rows, "cartCount_selected")
    cart_past = _sum_metric(rows, "cartCount_past")
    order_selected = _sum_metric(rows, "orderCount_selected")
    order_past = _sum_metric(rows, "orderCount_past")
    revenue_selected = _sum_metric(rows, "orderSum_selected")
    revenue_past = _sum_metric(rows, "orderSum_past")
    add_to_cart_selected = _weighted_percent(
        rows, "addToCartPercent_selected", "openCount_selected"
    )
    add_to_cart_past = _weighted_percent(
        rows, "addToCartPercent_past", "openCount_past"
    )
    cart_to_order_selected = _weighted_percent(
        rows, "cartToOrderPercent_selected", "cartCount_selected"
    )
    cart_to_order_past = _weighted_percent(
        rows, "cartToOrderPercent_past", "cartCount_past"
    )

    return [
        "ИТОГО",
        EMPTY_VALUE,
        EMPTY_VALUE,
        _format_metric(open_selected, "openCount_selected"),
        _format_metric(open_past, "openCount_past"),
        _format_delta(_dynamic(open_selected, open_past)),
        _format_metric(cart_selected, "cartCount_selected"),
        _format_metric(cart_past, "cartCount_past"),
        _format_delta(_dynamic(cart_selected, cart_past)),
        _format_metric(order_selected, "orderCount_selected"),
        _format_metric(order_past, "orderCount_past"),
        _format_delta(_dynamic(order_selected, order_past)),
        _format_metric(revenue_selected, "orderSum_selected"),
        _format_metric(revenue_past, "orderSum_past"),
        _format_delta(_dynamic(revenue_selected, revenue_past)),
        _format_metric(add_to_cart_selected, "addToCartPercent_selected"),
        _format_metric(add_to_cart_past, "addToCartPercent_past"),
        _format_delta(_dynamic(add_to_cart_selected, add_to_cart_past)),
        _format_metric(cart_to_order_selected, "cartToOrderPercent_selected"),
        _format_metric(cart_to_order_past, "cartToOrderPercent_past"),
        _format_delta(_dynamic(cart_to_order_selected, cart_to_order_past)),
        "",
    ]


def _display_rows(rows):
    if not rows:
        return [["Нет данных", *([EMPTY_VALUE] * (len(REPORT_COLUMNS) - 1))]]

    table_rows = [_table_row(row) for row in rows]
    table_rows.append(_total_row(rows))

    return table_rows


def _delta_color(value):
    if value is None:
        return NEUTRAL_COLOR

    if value <= -10:
        return NEGATIVE_COLOR

    if value <= 5:
        return NEUTRAL_COLOR

    return POSITIVE_COLOR


def _style_table(table, rows, total_row_index=None):
    for (row_index, column_index), cell in table.get_celld().items():
        cell.set_edgecolor(GRID_COLOR)
        cell.set_linewidth(0.45)
        cell.get_text().set_color(TEXT_COLOR)
        cell.get_text().set_wrap(True)

        if row_index == 0:
            cell.set_facecolor(HEADER_COLOR)
            cell.get_text().set_weight("bold")
            cell.get_text().set_fontsize(5.7)
            continue

        if total_row_index is not None and row_index == total_row_index:
            cell.set_facecolor(TOTAL_ROW_COLOR)
            cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor(ROW_COLOR)

        column_name = REPORT_COLUMNS[column_index]

        if column_name in DELTA_COLUMN_METRICS and row_index <= len(rows):
            row = rows[row_index - 1]
            cell.set_facecolor(_delta_color(row.get(DELTA_COLUMN_METRICS[column_name])))

        if column_index in (0, 1, 2, 21):
            cell.get_text().set_ha("left")
        else:
            cell.get_text().set_ha("right")


def _column_widths():
    weights = [
        0.15,
        0.075,
        0.06,
        0.052,
        0.052,
        0.052,
        0.052,
        0.052,
        0.052,
        0.052,
        0.052,
        0.052,
        0.066,
        0.066,
        0.052,
        0.072,
        0.072,
        0.058,
        0.072,
        0.072,
        0.058,
        0.12,
    ]
    total_weight = sum(weights)

    return [weight / total_weight for weight in weights]


def _report_date(rows):
    for row in rows:
        if row.get("date"):
            return str(row["date"])

    return datetime.now().strftime("%Y-%m-%d")


def generate_dashboard_image(funnel_df, problems_df, output_path):
    del problems_df

    rows = build_evidence_rows(funnel_df, limit=EVIDENCE_LIMIT_DASHBOARD)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cell_text = _display_rows(rows)
    report_date = _report_date(rows)
    title = (
        "WB Morning Brief — доказательство цифр\n"
        f"Продавец: {SELLER_NAME}\n"
        f"Дата: {report_date}"
    )

    plt.rcParams["font.family"] = "DejaVu Sans"
    figure_height = max(7.5, 2.7 + 0.5 * len(cell_text))
    figure, axis = plt.subplots(
        figsize=(36, figure_height), facecolor=BACKGROUND_COLOR, constrained_layout=True
    )
    axis.axis("off")
    axis.set_title(
        title, loc="left", fontsize=18, weight="bold", color=TITLE_COLOR, pad=18
    )

    table = axis.table(
        cellText=cell_text,
        colLabels=REPORT_COLUMNS,
        colColours=[HEADER_COLOR] * len(REPORT_COLUMNS),
        colWidths=_column_widths(),
        cellLoc="center",
        loc="upper left",
        bbox=[0, 0.07, 1, 0.85],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(6.1)
    table.scale(1, 1.55)
    total_row_index = len(cell_text) if rows else None
    _style_table(table, rows, total_row_index)

    axis.text(
        0,
        0.015,
        "Источник: WB API sales funnel. " "Динамика = (сейчас - было) / было × 100%.",
        transform=axis.transAxes,
        fontsize=11,
        color=TEXT_COLOR,
        ha="left",
        va="bottom",
    )

    figure.savefig(
        output_path, dpi=180, bbox_inches="tight", facecolor=BACKGROUND_COLOR
    )
    plt.close(figure)

    print("DASHBOARD IMAGE:")
    print(f"created: {output_path}")

    return output_path
