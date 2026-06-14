"""Adapters for importing Qbiki-style unit economics metrics."""

import os
from pathlib import Path

import pandas as pd

COLUMN_ALIASES = {
    "Заказы с 1000 показов": "ordersPer1000Impressions",
    "CR Органика": "organicCR",
    "CR Реклама": "adsCR",
    "Заказов с рекламы": "adsOrders",
    "Показы (Реклама)": "adsImpressions",
    "Показы рекламы": "adsImpressions",
    "CTR, % (Реклама)": "adsCTR",
    "CTR рекламы": "adsCTR",
    "Клики (Реклама)": "adsClicks",
    "Клики рекламы": "adsClicks",
    "Конверсия в корзину, % (Общая)": "cartConversion",
    "Конверсия в корзину": "cartConversion",
    "Конверсия в заказ, % (Общая)": "orderConversion",
    "Конверсия в заказ": "orderConversion",
    "Ставка средняя, руб (Реклама)": "avgAdBid",
    "Ставка средняя": "avgAdBid",
    "Прибыль с заказа (Реклама)": "adProfitPerOrder",
    "Прибыль с заказа рекламы": "adProfitPerOrder",
    "CPO (Реклама)": "CPO",
    "CPO": "CPO",
    "ДРР, %": "DRR",
    "ДРР": "DRR",
    "ДРР Чистый, %": "cleanDRR",
    "ДРР чистый": "cleanDRR",
    "МП чистая": "cleanMargin",
    "МП чистая органика": "cleanMarginOrganic",
    "МП чистая реклама": "cleanMarginAds",
    "ROI": "ROI",
    "Остаток WB": "wbStock",
    "Хватит на дней": "daysOfStock",
    "nm_id": "nmId",
    "nmID": "nmId",
    "Артикул WB": "nmId",
    "Артикул продавца": "vendorCode",
    "Наименование": "title",
    "Товар": "title",
}

NUMERIC_FIELDS = {
    "ordersPer1000Impressions",
    "organicCR",
    "adsCR",
    "adsOrders",
    "adsImpressions",
    "adsCTR",
    "adsClicks",
    "cartConversion",
    "orderConversion",
    "avgAdBid",
    "adProfitPerOrder",
    "CPO",
    "DRR",
    "cleanDRR",
    "cleanMargin",
    "cleanMarginOrganic",
    "cleanMarginAds",
    "ROI",
    "wbStock",
    "daysOfStock",
}


def _normalize_cell(value):
    if value in (None, ""):
        return value
    if not isinstance(value, str):
        return value
    cleaned = value.strip().replace("%", "").replace("₽", "").replace(" ", "")
    try:
        return float(cleaned.replace(",", "."))
    except ValueError:
        return value.strip()


def normalize_qbiki_row(row):
    normalized = {}
    for key, value in (row or {}).items():
        normalized_key = COLUMN_ALIASES.get(str(key).strip(), str(key).strip())
        normalized[normalized_key] = (
            _normalize_cell(value) if normalized_key in NUMERIC_FIELDS else value
        )
    return normalized


def normalize_qbiki_rows(rows):
    return [normalize_qbiki_row(row) for row in rows or [] if isinstance(row, dict)]


def load_qbiki_file(path):
    source = Path(path)
    if not source.exists():
        print(f"QBIKI DATA WARNING: file not found: {source}")
        return []
    if source.suffix.lower() in {".xlsx", ".xls"}:
        dataframe = pd.read_excel(source).fillna("")
    else:
        dataframe = pd.read_csv(source).fillna("")
    return normalize_qbiki_rows(dataframe.to_dict("records"))


def collect_qbiki_metrics(source=None, rows=None):
    """Collect normalized Qbiki metrics from provided rows, CSV/XLSX, or future source."""
    if rows is not None:
        return normalize_qbiki_rows(rows)
    source = (
        source
        or os.getenv("QBIKI_METRICS_PATH")
        or os.getenv("QBIKI_GOOGLE_SHEETS_EXPORT")
    )
    if source:
        return load_qbiki_file(source)
    return []
