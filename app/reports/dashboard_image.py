from datetime import datetime
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

DASHBOARD_COLUMNS = [
    "Товар",
    "Артикул WB",
    "Переходы",
    "Корзины",
    "Заказы",
    "Выручка",
    "Δ Заказы %",
    "Δ Выручка %",
]
TOP_SKU_LIMIT = 10
GROWTH_COLOR = "#DCFCE7"
DROP_COLOR = "#FEE2E2"
WEAK_COLOR = "#FEF3C7"
HEADER_COLOR = "#111827"
TEXT_COLOR = "#1F2937"
MUTED_TEXT_COLOR = "#6B7280"
CARD_EDGE_COLOR = "#E5E7EB"
CARD_FACE_COLOR = "#FFFFFF"
BACKGROUND_COLOR = "#F8FAFC"


def _records_dataframe(dataframe):
    if dataframe is None:
        return pd.DataFrame()

    if isinstance(dataframe, pd.DataFrame):
        return dataframe.copy()

    return pd.DataFrame(dataframe)


def _first_existing_column(dataframe, columns):
    for column in columns:
        if column in dataframe.columns:
            return column

    return None


def _numeric_series(dataframe, column):
    if column is None or column not in dataframe.columns:
        return pd.Series([0] * len(dataframe), index=dataframe.index, dtype="float64")

    return pd.to_numeric(dataframe[column], errors="coerce").fillna(0)


def _format_number(value, suffix=""):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value or "0")

    if number.is_integer():
        formatted = f"{int(number):,}".replace(",", " ")
    else:
        formatted = f"{number:,.1f}".replace(",", " ").replace(".", ",")

    return f"{formatted}{suffix}"


def _format_percent(value):
    if value in (None, ""):
        return "n/a"

    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _dynamic_color(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return WEAK_COLOR

    if number >= 5:
        return GROWTH_COLOR

    if number <= -5:
        return DROP_COLOR

    return WEAK_COLOR


def _metric_dynamic_map(problems_df, metric):
    if problems_df.empty or "metric" not in problems_df.columns:
        return {}

    nm_column = _first_existing_column(problems_df, ["nmId", "Артикул WB"])
    dynamic_column = _first_existing_column(
        problems_df, ["dynamicPercent", "Динамика", "Δ", "delta"]
    )

    if nm_column is None or dynamic_column is None:
        return {}

    metric_rows = problems_df[problems_df["metric"].astype(str) == metric]

    return {
        str(row[nm_column]): row[dynamic_column]
        for _, row in metric_rows.iterrows()
        if pd.notna(row[nm_column])
    }


def _build_top_sku_table(funnel_df, problems_df):
    if funnel_df.empty:
        return pd.DataFrame(columns=DASHBOARD_COLUMNS)

    title_column = _first_existing_column(
        funnel_df, ["title", "Товар", "product.title"]
    )
    nm_column = _first_existing_column(
        funnel_df, ["nmId", "Артикул WB", "product.nmId"]
    )
    open_column = _first_existing_column(funnel_df, ["openCount", "Переходы"])
    cart_column = _first_existing_column(funnel_df, ["cartCount", "Корзины"])
    order_column = _first_existing_column(funnel_df, ["orderCount", "Заказы"])
    revenue_column = _first_existing_column(funnel_df, ["orderSum", "Выручка"])
    orders_dynamic = _metric_dynamic_map(problems_df, "orderCount")
    revenue_dynamic = _metric_dynamic_map(problems_df, "orderSum")

    table_df = pd.DataFrame(
        {
            "Товар": funnel_df[title_column] if title_column else "Без названия",
            "Артикул WB": funnel_df[nm_column] if nm_column else "n/a",
            "Переходы": _numeric_series(funnel_df, open_column),
            "Корзины": _numeric_series(funnel_df, cart_column),
            "Заказы": _numeric_series(funnel_df, order_column),
            "Выручка": _numeric_series(funnel_df, revenue_column),
        }
    )
    table_df["_sort_revenue"] = table_df["Выручка"]
    table_df["_sort_orders"] = table_df["Заказы"]
    table_df = table_df.sort_values(
        ["_sort_revenue", "_sort_orders", "Переходы"], ascending=False
    ).head(TOP_SKU_LIMIT)
    table_df["Δ Заказы %"] = table_df["Артикул WB"].astype(str).map(orders_dynamic)
    table_df["Δ Выручка %"] = table_df["Артикул WB"].astype(str).map(revenue_dynamic)

    return table_df[DASHBOARD_COLUMNS].fillna("")


def _build_summary(funnel_df, problems_df):
    nm_column = _first_existing_column(
        funnel_df, ["nmId", "Артикул WB", "product.nmId"]
    )
    revenue_column = _first_existing_column(funnel_df, ["orderSum", "Выручка"])
    problem_nm_column = _first_existing_column(problems_df, ["nmId", "Артикул WB"])

    total_sku = funnel_df[nm_column].nunique() if nm_column else len(funnel_df)
    problem_sku = problems_df[problem_nm_column].nunique() if problem_nm_column else 0
    total_revenue = _numeric_series(funnel_df, revenue_column).sum()
    order_dynamic_values = list(_metric_dynamic_map(problems_df, "orderCount").values())
    order_dynamic = (
        pd.to_numeric(pd.Series(order_dynamic_values), errors="coerce").dropna().mean()
        if order_dynamic_values
        else 0
    )

    return {
        "Всего SKU": _format_number(total_sku),
        "Проблемных SKU": _format_number(problem_sku),
        "Общая выручка": _format_number(total_revenue, " ₽"),
        "Динамика заказов": _format_percent(order_dynamic),
    }


def _shorten_text(value, limit=28):
    text = str(value or "")

    return text if len(text) <= limit else f"{text[: limit - 1]}…"


def _table_cell_text(top_sku_df):
    rows = []

    for _, row in top_sku_df.iterrows():
        rows.append(
            [
                _shorten_text(row["Товар"]),
                str(row["Артикул WB"]),
                _format_number(row["Переходы"]),
                _format_number(row["Корзины"]),
                _format_number(row["Заказы"]),
                _format_number(row["Выручка"], " ₽"),
                _format_percent(row["Δ Заказы %"]),
                _format_percent(row["Δ Выручка %"]),
            ]
        )

    if rows:
        return rows

    return [["Нет данных", "", "", "", "", "", "", ""]]


def _draw_summary_cards(axis, summary):
    axis.axis("off")
    positions = [0.02, 0.265, 0.51, 0.755]

    for (label, value), x_position in zip(summary.items(), positions):
        card = FancyBboxPatch(
            (x_position, 0.08),
            0.22,
            0.78,
            boxstyle="round,pad=0.018,rounding_size=0.035",
            linewidth=1,
            edgecolor=CARD_EDGE_COLOR,
            facecolor=CARD_FACE_COLOR,
        )
        axis.add_patch(card)
        axis.text(
            x_position + 0.025,
            0.62,
            label,
            fontsize=11,
            color=MUTED_TEXT_COLOR,
            weight="bold",
            transform=axis.transAxes,
        )
        axis.text(
            x_position + 0.025,
            0.28,
            value,
            fontsize=17,
            color=TEXT_COLOR,
            weight="bold",
            transform=axis.transAxes,
        )


def _draw_dynamics(axis, top_sku_df):
    axis.set_title("Динамика TOP SKU", loc="left", fontsize=14, weight="bold")

    if top_sku_df.empty:
        axis.text(0.5, 0.5, "Нет данных для динамики", ha="center", va="center")
        axis.axis("off")
        return

    dynamics = pd.to_numeric(top_sku_df["Δ Заказы %"], errors="coerce").fillna(0)
    labels = top_sku_df["Артикул WB"].astype(str).tolist()
    colors = [_dynamic_color(value) for value in dynamics]
    bars = axis.barh(labels, dynamics, color=colors, edgecolor="#CBD5E1")
    axis.axvline(0, color="#94A3B8", linewidth=1)
    axis.grid(axis="x", color="#E5E7EB", linestyle="--", linewidth=0.7)
    axis.set_xlabel("Δ Заказы, %")
    axis.invert_yaxis()

    for bar, value in zip(bars, dynamics):
        axis.text(
            value + (1 if value >= 0 else -1),
            bar.get_y() + bar.get_height() / 2,
            _format_percent(value),
            va="center",
            ha="left" if value >= 0 else "right",
            fontsize=9,
            color=TEXT_COLOR,
        )


def _draw_top_table(axis, top_sku_df):
    axis.axis("off")
    axis.set_title("TOP SKU", loc="left", fontsize=14, weight="bold", pad=12)
    table = axis.table(
        cellText=_table_cell_text(top_sku_df),
        colLabels=DASHBOARD_COLUMNS,
        colColours=[HEADER_COLOR] * len(DASHBOARD_COLUMNS),
        colWidths=[0.23, 0.11, 0.1, 0.09, 0.08, 0.13, 0.12, 0.14],
        cellLoc="center",
        loc="upper center",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.55)

    for (row_index, column_index), cell in table.get_celld().items():
        cell.set_edgecolor("#E2E8F0")

        if row_index == 0:
            cell.get_text().set_color("white")
            cell.get_text().set_weight("bold")
            continue

        cell.set_facecolor("#FFFFFF" if row_index % 2 else "#F8FAFC")

        if column_index == 0:
            cell.get_text().set_ha("left")

        if column_index in (6, 7) and not top_sku_df.empty:
            value = top_sku_df.iloc[row_index - 1, column_index]
            cell.set_facecolor(_dynamic_color(value))
            cell.get_text().set_weight("bold")


def generate_dashboard_image(funnel_df, problems_df, output_path):
    funnel_df = _records_dataframe(funnel_df)
    problems_df = _records_dataframe(problems_df)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    top_sku_df = _build_top_sku_table(funnel_df, problems_df)
    summary = _build_summary(funnel_df, problems_df)
    report_date = datetime.now().strftime("%d.%m.%Y")

    plt.rcParams["font.family"] = "DejaVu Sans"
    figure = plt.figure(figsize=(16, 10), facecolor=BACKGROUND_COLOR)
    grid = figure.add_gridspec(
        4,
        1,
        height_ratios=[0.9, 1.25, 2.1, 4.2],
        hspace=0.42,
    )

    header_axis = figure.add_subplot(grid[0])
    header_axis.axis("off")
    header_axis.text(
        0.02,
        0.72,
        "WB Morning Brief",
        fontsize=28,
        weight="bold",
        color=HEADER_COLOR,
        transform=header_axis.transAxes,
    )
    header_axis.text(
        0.02,
        0.28,
        f"ИП Череватенко Б.С. · {report_date}",
        fontsize=14,
        color=MUTED_TEXT_COLOR,
        transform=header_axis.transAxes,
    )

    summary_axis = figure.add_subplot(grid[1])
    _draw_summary_cards(summary_axis, summary)

    dynamics_axis = figure.add_subplot(grid[2])
    _draw_dynamics(dynamics_axis, top_sku_df)

    table_axis = figure.add_subplot(grid[3])
    _draw_top_table(table_axis, top_sku_df)

    figure.savefig(
        output_path, dpi=180, bbox_inches="tight", facecolor=BACKGROUND_COLOR
    )
    plt.close(figure)

    return output_path
