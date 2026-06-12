from datetime import datetime
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

REPORT_COLUMNS = [
    "Артикул продавца",
    "Артикул WB",
    "Показы",
    "CTR",
    "CTR CPC",
    "Ставки АРК было",
    "Ставки АРК стало",
    "Аукцион/СРС было",
    "Аукцион/СРС стало",
    "Переходы в карточку",
    "Положили в корзину",
    "Заказали товаров, шт",
    "Выкупили, шт",
    "Конверсия в корзину, %",
    "Конверсия в заказ, %",
    "Процент выкупа",
    "Заказали на сумму, ₽",
    "Выкупили на сумму, ₽",
    "Отменили на сумму, ₽",
]
TOP_ROWS_LIMIT = 15
EMPTY_VALUE = ""
HEADER_COLOR = "#E5E7EB"
ROW_COLOR = "#FFFFFF"
ALT_ROW_COLOR = "#F8FAFC"
TOTAL_ROW_COLOR = "#DCFCE7"
GRID_COLOR = "#CBD5E1"
TITLE_COLOR = "#111827"
TEXT_COLOR = "#1F2937"
BACKGROUND_COLOR = "#FFFFFF"


NUMERIC_REPORT_COLUMNS = [
    "Показы",
    "Переходы в карточку",
    "Положили в корзину",
    "Заказали товаров, шт",
    "Выкупили, шт",
    "Заказали на сумму, ₽",
    "Выкупили на сумму, ₽",
    "Отменили на сумму, ₽",
]
PERCENT_REPORT_COLUMNS = [
    "CTR",
    "Конверсия в корзину, %",
    "Конверсия в заказ, %",
    "Процент выкупа",
]
SUM_REPORT_COLUMNS = [
    "Показы",
    "Переходы в карточку",
    "Положили в корзину",
    "Заказали товаров, шт",
    "Выкупили, шт",
    "Заказали на сумму, ₽",
    "Выкупили на сумму, ₽",
    "Отменили на сумму, ₽",
]
MEAN_REPORT_COLUMNS = [
    "CTR",
    "Конверсия в корзину, %",
    "Конверсия в заказ, %",
    "Процент выкупа",
]


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


def _column_or_empty(dataframe, columns):
    column = _first_existing_column(dataframe, columns)

    if column is None:
        return pd.Series(
            [pd.NA] * len(dataframe), index=dataframe.index, dtype="object"
        )

    return dataframe[column]


def _numeric_series(dataframe, columns):
    return pd.to_numeric(_column_or_empty(dataframe, columns), errors="coerce")


def _format_number(value):
    if pd.isna(value):
        return EMPTY_VALUE

    number = float(value)

    if number.is_integer():
        return f"{int(number):,}".replace(",", " ")

    return f"{number:,.1f}".replace(",", " ").replace(".", ",")


def _format_percent(value):
    if pd.isna(value):
        return EMPTY_VALUE

    return f"{float(value):.1f}%".replace(".", ",")


def _format_cell(column, value):
    if column in PERCENT_REPORT_COLUMNS:
        return _format_percent(value)

    if column in NUMERIC_REPORT_COLUMNS:
        return _format_number(value)

    if pd.isna(value):
        return EMPTY_VALUE

    return str(value)


def _safe_ratio_percent(numerator, denominator):
    return (numerator / denominator * 100).where(denominator > 0)


def _build_report_dataframe(funnel_df):
    if funnel_df.empty:
        return pd.DataFrame(columns=REPORT_COLUMNS)

    impressions = _numeric_series(funnel_df, ["impressions", "views", "openCount"])
    opens = _numeric_series(funnel_df, ["openCount"])
    order_sum = _numeric_series(funnel_df, ["orderSum"])

    report_df = pd.DataFrame(
        {
            "Артикул продавца": _column_or_empty(funnel_df, ["vendorCode"]),
            "Артикул WB": _column_or_empty(funnel_df, ["nmId"]),
            "Показы": impressions,
            "CTR": _safe_ratio_percent(opens, impressions),
            "CTR CPC": pd.NA,
            "Ставки АРК было": pd.NA,
            "Ставки АРК стало": pd.NA,
            "Аукцион/СРС было": pd.NA,
            "Аукцион/СРС стало": pd.NA,
            "Переходы в карточку": opens,
            "Положили в корзину": _numeric_series(funnel_df, ["cartCount"]),
            "Заказали товаров, шт": _numeric_series(funnel_df, ["orderCount"]),
            "Выкупили, шт": _numeric_series(funnel_df, ["buyoutCount"]),
            "Конверсия в корзину, %": _numeric_series(funnel_df, ["addToCartPercent"]),
            "Конверсия в заказ, %": _numeric_series(funnel_df, ["cartToOrderPercent"]),
            "Процент выкупа": _numeric_series(funnel_df, ["buyoutPercent"]),
            "Заказали на сумму, ₽": order_sum,
            "Выкупили на сумму, ₽": _numeric_series(funnel_df, ["buyoutSum"]),
            "Отменили на сумму, ₽": _numeric_series(funnel_df, ["cancelSum"]),
        }
    )

    return report_df.sort_values("Заказали на сумму, ₽", ascending=False).head(
        TOP_ROWS_LIMIT
    )


def _build_total_row(report_df):
    total_row = {column: pd.NA for column in REPORT_COLUMNS}
    total_row["Артикул продавца"] = "ИТОГО"

    for column in SUM_REPORT_COLUMNS:
        total_row[column] = pd.to_numeric(report_df[column], errors="coerce").sum(
            min_count=1
        )

    for column in MEAN_REPORT_COLUMNS:
        total_row[column] = pd.to_numeric(report_df[column], errors="coerce").mean()

    return total_row


def _display_rows(report_df):
    if report_df.empty:
        return [["Нет данных", *([EMPTY_VALUE] * (len(REPORT_COLUMNS) - 1))]]

    rows = []
    for _, row in report_df.iterrows():
        rows.append([_format_cell(column, row[column]) for column in REPORT_COLUMNS])

    total_row = _build_total_row(report_df)
    rows.append([_format_cell(column, total_row[column]) for column in REPORT_COLUMNS])

    return rows


def _style_table(table, total_row_index=None):
    for (row_index, column_index), cell in table.get_celld().items():
        cell.set_edgecolor(GRID_COLOR)
        cell.set_linewidth(0.6)
        cell.get_text().set_color(TEXT_COLOR)
        cell.get_text().set_wrap(True)

        if row_index == 0:
            cell.set_facecolor(HEADER_COLOR)
            cell.get_text().set_weight("bold")
            cell.get_text().set_fontsize(7.4)
            continue

        if total_row_index is not None and row_index == total_row_index:
            cell.set_facecolor(TOTAL_ROW_COLOR)
            cell.get_text().set_weight("bold")
        else:
            cell.set_facecolor(ROW_COLOR if row_index % 2 else ALT_ROW_COLOR)

        if column_index in (0, 1):
            cell.get_text().set_ha("left")
        else:
            cell.get_text().set_ha("right")


def _column_widths():
    weights = [
        0.085,
        0.06,
        0.052,
        0.044,
        0.044,
        0.057,
        0.057,
        0.062,
        0.062,
        0.068,
        0.066,
        0.07,
        0.054,
        0.07,
        0.07,
        0.057,
        0.074,
        0.074,
        0.074,
    ]
    total_weight = sum(weights)

    return [weight / total_weight for weight in weights]


def generate_dashboard_image(funnel_df, problems_df, output_path):
    del problems_df

    funnel_df = _records_dataframe(funnel_df)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    original_row_count = len(funnel_df)
    report_df = _build_report_dataframe(funnel_df)
    cell_text = _display_rows(report_df)
    report_date = datetime.now().strftime("%d.%m.%Y")
    title_suffix = (
        "TOP 15 по сумме заказов" if original_row_count > TOP_ROWS_LIMIT else ""
    )
    title = f"WB аналитический отчёт · {report_date}"

    if title_suffix:
        title = f"{title} · {title_suffix}"

    plt.rcParams["font.family"] = "DejaVu Sans"
    figure_height = max(6.5, 2.4 + 0.42 * len(cell_text))
    figure, axis = plt.subplots(
        figsize=(30, figure_height), facecolor=BACKGROUND_COLOR, constrained_layout=True
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
        bbox=[0, 0, 1, 0.94],
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.8)
    table.scale(1, 1.45)
    total_row_index = len(cell_text) if not report_df.empty else None
    _style_table(table, total_row_index)

    figure.savefig(
        output_path, dpi=180, bbox_inches="tight", facecolor=BACKGROUND_COLOR
    )
    plt.close(figure)

    return output_path
