"""Streamlit Executive Dashboard for Morning Brief."""

import logging
import sys
from datetime import date
from html import escape
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
APP_DIR = PROJECT_ROOT / "app"

for path in (str(CURRENT_DIR), str(APP_DIR), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from urllib.parse import quote

import pandas as pd
import streamlit as st

from core import date_engine


def _get_date_engine_function(name):
    helper = getattr(date_engine, name, None)
    if not callable(helper):
        raise RuntimeError(f"core.date_engine.{name} is required for Dashboard date handling")
    return helper


align_time_series = _get_date_engine_function("align_time_series")
normalize_report_date = _get_date_engine_function("normalize_report_date")
to_business_date = _get_date_engine_function("to_business_date")


def closest_available_date(available_dates, selected_date, max_shift_days=3):
    """Return closest available date, with a local fallback for older date_engine builds."""
    helper = getattr(date_engine, "closest_available_date", None)
    if helper is not None:
        return helper(available_dates, selected_date, max_shift_days=max_shift_days)

    selected = normalize_report_date(selected_date)
    if selected is None:
        return None

    candidates = []
    for value in available_dates or []:
        normalized = normalize_report_date(value)
        if normalized is None:
            continue
        shift = abs((normalized - selected).days)
        if shift <= max_shift_days:
            candidates.append((shift, normalized))

    if not candidates:
        return None
    return min(candidates)[1].isoformat()


def debug_date_filter(rows, selected_date, date_field="report_date", filtered_count=None):
    """Return date-filter diagnostics, with a no-op fallback for older date_engine builds."""
    helper = getattr(date_engine, "debug_date_filter", None)
    if helper is not None:
        return helper(
            rows,
            selected_date,
            date_field=date_field,
            filtered_count=filtered_count,
        )

    return {
        "reason": None,
        "selected_date": selected_date,
        "min_report_date": None,
        "max_report_date": None,
        "rows_before_filter": len(rows or []),
        "rows_after_filter": filtered_count,
        "report_date_dtype": "unknown",
        "rows_in_plus_minus_3_days": 0,
        "date_field": date_field,
    }
from formatters import (
    format_money,
    format_number,
    lost_orders,
    lost_revenue,
    main_reason,
    matches_reason_filter,
    reason_explanation,
    reason_loss_summary,
    CONFIRMATION_COLUMN_HELP,
    MAIN_REASON_HELP,
    REASON_DESCRIPTION_HELP,
    REASON_FILTER_OPTIONS,
    prepare_seller_table,
    prepare_sku_table,
)
from wb_dashboard_queries import (
    check_dashboard_connection,
    dataframe_for_display,
    fetch_data_quality,
    fetch_problems,
    fetch_problems_diagnostics,
    fetch_report_dates,
    fetch_sellers,
    find_product_by_nm_id,
    is_change_log_available,
)
from supabase_client import get_supabase_client, get_supabase_credentials_info
from sku_page import render_sku_page


logger = logging.getLogger(__name__)

def metric_tooltip(title, how, sources, check, limits):
    return (
        f"{title}\n\n"
        f"Как считается:\n{how}\n\n"
        f"Откуда данные:\n{sources}\n\n"
        f"Как проверить в Wildberries:\n{check}\n\n"
        f"Ограничения:\n{limits}"
    )


LOST_REVENUE_HELP = metric_tooltip(
    "Потеря выручки",
    "Потерянные заказы × средний чек или сохранённая разница выручки между периодами.",
    "Funnel API: продажи, заказы и воронка по SKU; сохранённые проблемы Dashboard.",
    "WB кабинет → Продажи → выбрать nm_id → сравнить выручку и заказы за одинаковые периоды.",
    "Если нет базы сравнения или данные по SKU неполные, сумма может быть приблизительной.",
)
LOST_ORDERS_HELP = metric_tooltip(
    "Потеря заказов",
    "Сумма потерянных заказов по проблемным SKU за выбранную дату или период.",
    "Funnel API: заказы и воронка по SKU; сохранённые проблемы Dashboard.",
    "WB кабинет → Продажи → выбрать nm_id → сравнить количество заказов за одинаковые периоды.",
    "Если прошлый период отсутствует, используется сохранённая оценка; при неполных данных нужна ручная сверка.",
)
MAIN_REASON_TOOLTIP = metric_tooltip(
    "Главная причина",
    "Выбирается причина с наибольшим вкладом в потери среди проблемных SKU.",
    "Funnel API, Ads API, Stocks API и Change Log по товарам.",
    "В WB/JEM проверить метрику, которая подтверждает причину: продажи, конверсию, рекламу, остатки или изменения карточки.",
    "Это управленческая подсказка, а не окончательный диагноз; если данных мало, причину нужно подтвердить вручную.",
)
REASON_LOSS_TOOLTIP = metric_tooltip(
    "Потери по причинам",
    "Сумма потерь по SKU, где причина назначена основной. Доля = потери причины ÷ все потери × 100.",
    "Сохранённые проблемы SKU, Funnel API, Ads API, Stocks API и Change Log.",
    "Открыть SKU из причины и сверить в WB/JEM продажи, воронку, рекламные показатели, остатки и последние изменения.",
    "Если причина неоднозначна или часть данных не пришла, распределение потерь по причинам может быть приблизительным.",
)
FIRST_LOOK_HELP = metric_tooltip(
    "Что смотреть первым",
    "SKU выбирается по максимальной потере выручки среди текущих проблемных товаров.",
    "Сохранённые проблемы Dashboard и Funnel API по SKU.",
    "Открыть указанный nm_id в WB/JEM и сверить продажи, заказы, конверсию, рекламу и остатки за дату отчёта.",
    "Если данные по продавцу или SKU неполные, товар может требовать дополнительной ручной проверки.",
)

def tooltip_text(text):
    return escape(str(text), quote=True).replace("\n", "&#10;")


def help_icon(help_text):
    return f'<span title="{tooltip_text(help_text)}">ⓘ</span>'


def main_reason_help(reason):
    return MAIN_REASON_TOOLTIP + "\n\n" + MAIN_REASON_HELP


def reason_loss_help(reason_summary):
    loss_value = (
        format_money(reason_summary["lost_revenue"])
        if reason_summary["metric_key"] == "lost_revenue"
        else f"{format_number(round(reason_summary['lost_orders']))} заказов"
    )
    return (
        REASON_LOSS_TOOLTIP + "\n\n"
        f"{reason_summary['reason'].capitalize()} — {loss_value} "
        f"({format_number(round(reason_summary['share']))}%).\n\n"
        "Расчёт:\n"
        f"сумма потерь по всем SKU, где основной причиной определена {reason_summary['reason']}.\n\n"
        f"{format_number(round(reason_summary['share']))}% означает долю этой причины "
        "от общей суммы потерь.\n\n"
        f"Количество SKU: {format_number(reason_summary['sku_count'])}"
    )



def _fetch_ads_rows(table_name, columns, seller_id=None):
    try:
        query = get_supabase_client().table(table_name).select(columns).limit(10000)
        if seller_id:
            query = query.eq("seller_id", seller_id)
        return query.execute().data or []
    except Exception:
        return []


CAMPAIGN_TYPE_LABELS = {
    "manual": "Аукцион",
    "unified": "Автокампания",
    "search": "Поиск",
    "catalog": "Каталог",
}


def _campaign_display_name(campaign_id, campaign_name):
    name = str(campaign_name or "").strip()
    if not name or name.lower() == "unknown":
        return f"Кампания {campaign_id}"
    return name


def _campaign_type_display_name(campaign_type):
    campaign_type_value = str(campaign_type or "").strip()
    if not campaign_type_value:
        return "Тип неизвестен"
    return CAMPAIGN_TYPE_LABELS.get(campaign_type_value.lower(), campaign_type_value)


def _campaign_option_display_name(campaign_id, campaign_name, campaign_type):
    campaign_type_name = _campaign_type_display_name(campaign_type)
    if campaign_name == f"Кампания {campaign_id}":
        return f"{campaign_name} | {campaign_type_name}"
    return f"{campaign_name} | {campaign_type_name} | {campaign_id}"


def _normalize_filter_id(value):
    value = str(value or "").strip()
    if value.endswith(".0"):
        value = value[:-2]
    return value


def _empty_ads_cluster_debug(seller_id, campaign_id, start_date, end_date):
    return {
        "selected_seller_id": _normalize_filter_id(seller_id),
        "selected_campaign_id": _normalize_filter_id(campaign_id),
        "selected_campaign_name": "",
        "selected_start_date": str(start_date or ""),
        "selected_end_date": str(end_date or ""),
        "rows_loaded_from_supabase": 0,
        "rows_after_seller_filter": 0,
        "rows_after_campaign_filter": 0,
        "exists_in_daily_ads_metrics": False,
        "exists_in_ads_clusters_daily": False,
        "available_cluster_campaign_ids": [],
        "rows_after_date_filter": 0,
        "rows_after_text_filter": 0,
        "rows_before_orders_filter": 0,
        "rows_after_orders_filter": 0,
        "max_orders_count": 0,
        "rows_final": 0,
    }


@st.cache_data(ttl=300)
def fetch_ads_cluster_sellers():
    rows = []
    rows.extend(_fetch_ads_rows("ads_clusters_daily", "seller_id,seller_name"))
    rows.extend(_fetch_ads_rows("ads_campaigns", "seller_id,seller_name"))
    rows.extend(_fetch_ads_rows("ads_campaigns_cache", "seller_id"))

    sellers_map = {}
    for row in rows:
        seller_id = row.get("seller_id")
        if seller_id is None:
            continue
        seller_id = str(seller_id)
        seller_name = str(row.get("seller_name") or "").strip()
        sellers_map.setdefault(seller_id, seller_name or seller_id)
        if seller_name and sellers_map[seller_id] == seller_id:
            sellers_map[seller_id] = seller_name
    return sorted(
        [{"seller_id": key, "seller_name": value} for key, value in sellers_map.items()],
        key=lambda row: row["seller_name"].lower(),
    )


def _fetch_ads_cluster_campaign_rows(seller_id):
    rows = []
    offset = 0
    page_size = 1000
    try:
        while True:
            page = (
                get_supabase_client()
                .table("daily_ads_metrics")
                .select("campaign_id,campaign_name,campaign_type")
                .eq("seller_id", seller_id)
                .range(offset, offset + page_size - 1)
                .execute()
                .data
                or []
            )
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows
    except Exception:
        return []


@st.cache_data(ttl=300)
def fetch_ads_cluster_campaigns(seller_id, start_date, end_date):
    debug = {
        "campaigns_source": "daily_ads_metrics",
        "campaign_list_source": "daily_ads_metrics",
        "selected_seller_id": _normalize_filter_id(seller_id),
        "selected_start_date": str(start_date or ""),
        "selected_end_date": str(end_date or ""),
        "campaigns_found": 0,
        "campaigns_loaded": 0,
        "campaign_ids_loaded": [],
    }
    if not seller_id:
        return [], debug

    rows = _fetch_ads_cluster_campaign_rows(seller_id)
    source = "daily_ads_metrics"

    campaigns_map = {}
    for row in rows:
        campaign_id = row.get("campaign_id")
        if campaign_id is None:
            continue
        campaign_id = _normalize_filter_id(campaign_id)
        campaign_name = _campaign_display_name(campaign_id, row.get("campaign_name"))
        campaign_type = str(row.get("campaign_type") or "").strip()
        current = campaigns_map.get(campaign_id, {})
        current_name = current.get("campaign_name")
        if not current_name or current_name == f"Кампания {campaign_id}":
            current_name = campaign_name
        current_type = current.get("campaign_type") or campaign_type
        campaigns_map[campaign_id] = {
            "campaign_id": campaign_id,
            "campaign_name": current_name,
            "campaign_type": current_type,
            "display_name": _campaign_option_display_name(campaign_id, current_name, current_type),
            "campaign_list_source": source,
            "campaigns_source": source,
        }

    campaigns = sorted(
        campaigns_map.values(),
        key=lambda row: (str(row["campaign_name"]).lower(), row["campaign_id"]),
    )
    debug["campaigns_source"] = source
    debug["campaign_list_source"] = source
    debug["campaigns_found"] = len(campaigns)
    debug["campaigns_loaded"] = len(campaigns)
    debug["campaign_ids_loaded"] = [row["campaign_id"] for row in campaigns]
    return campaigns, debug


@st.cache_data(ttl=300)
def find_ads_cluster_campaign(seller_id, campaign_search):
    campaign_search = str(campaign_search or "").strip()
    if not seller_id or not campaign_search:
        return None

    normalized_search = _normalize_filter_id(campaign_search)
    digits_search = "".join(char for char in campaign_search if char.isdigit())
    campaign_id_for_lookup = digits_search or normalized_search

    rows = []
    source = ""
    for table_name in ("ads_clusters_daily", "daily_ads_metrics"):
        try:
            rows = (
                get_supabase_client()
                .table(table_name)
                .select("seller_id,seller_name,campaign_id,campaign_name,campaign_type")
                .eq("seller_id", seller_id)
                .eq("campaign_id", campaign_id_for_lookup)
                .limit(1)
                .execute()
                .data
                or []
            )
        except Exception:
            rows = []
        if rows:
            source = table_name
            break

    for row in rows:
        campaign_id = _normalize_filter_id(row.get("campaign_id"))
        campaign_name = _campaign_display_name(campaign_id, row.get("campaign_name"))
        campaign_type = str(row.get("campaign_type") or "").strip()
        return {
            "seller_id": str(row.get("seller_id") or seller_id),
            "seller_name": str(row.get("seller_name") or "").strip(),
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "campaign_type": campaign_type,
            "display_name": _campaign_option_display_name(campaign_id, campaign_name, campaign_type),
            "data_source": source,
        }
    return None


@st.cache_data(ttl=300)
def campaign_exists_in_daily_ads_metrics(seller_id, campaign_id):
    if not seller_id or not campaign_id:
        return False
    try:
        rows = (
            get_supabase_client()
            .table("daily_ads_metrics")
            .select("campaign_id")
            .eq("seller_id", seller_id)
            .eq("campaign_id", _normalize_filter_id(campaign_id))
            .limit(1)
            .execute()
            .data
            or []
        )
    except Exception:
        rows = []
    return bool(rows)


@st.cache_data(ttl=300)
def fetch_ads_cluster_rows(seller_id, campaign_id, start_date, end_date):
    if not seller_id or not campaign_id or not start_date or not end_date:
        return [], _empty_ads_cluster_debug(seller_id, campaign_id, start_date, end_date)

    debug = _empty_ads_cluster_debug(seller_id, campaign_id, start_date, end_date)
    selected_seller_id = _normalize_filter_id(seller_id)
    selected_campaign_id = _normalize_filter_id(campaign_id)
    selected_start_date = normalize_report_date(start_date)
    selected_end_date = normalize_report_date(end_date)

    rows = (
        get_supabase_client()
        .table("ads_clusters_daily")
        .select(
            "seller_id,campaign_id,campaign_name,campaign_type,report_date,cluster,impressions,clicks,ctr,cpc,spend,cart_count,orders_count"
        )
        .limit(10000)
        .execute()
        .data
        or []
    )
    debug["rows_loaded_from_supabase"] = len(rows)

    rows_after_seller = [
        row for row in rows if _normalize_filter_id(row.get("seller_id")) == selected_seller_id
    ]
    debug["rows_after_seller_filter"] = len(rows_after_seller)
    debug["available_cluster_campaign_ids"] = sorted(
        {
            _normalize_filter_id(row.get("campaign_id"))
            for row in rows_after_seller
            if row.get("campaign_id") is not None
        }
    )

    rows_after_campaign = [
        row
        for row in rows_after_seller
        if _normalize_filter_id(row.get("campaign_id")) == selected_campaign_id
    ]
    debug["rows_after_campaign_filter"] = len(rows_after_campaign)
    debug["exists_in_ads_clusters_daily"] = bool(rows_after_campaign)
    debug["exists_in_daily_ads_metrics"] = campaign_exists_in_daily_ads_metrics(
        selected_seller_id,
        selected_campaign_id,
    )

    rows_after_date = []
    for row in rows_after_campaign:
        report_date = normalize_report_date(row.get("report_date"))
        if report_date is None or selected_start_date is None or selected_end_date is None:
            continue
        if selected_start_date <= report_date <= selected_end_date:
            rows_after_date.append(row)
    debug["rows_after_date_filter"] = len(rows_after_date)

    if rows_after_date:
        debug["selected_campaign_name"] = _campaign_display_name(
            campaign_id,
            rows_after_date[0].get("campaign_name"),
        )

    logger.info("DEBUG ADS CLUSTERS %s", debug)
    return rows_after_date, debug


@st.cache_data(ttl=300)
def fetch_ads_cluster_available_dates(seller_id=None, campaign_id=None):
    query = (
        get_supabase_client()
        .table("ads_clusters_daily")
        .select("report_date")
        .order("report_date", desc=True)
        .limit(10000)
    )
    if seller_id:
        query = query.eq("seller_id", seller_id)
    if campaign_id:
        query = query.eq("campaign_id", campaign_id)
    rows = query.execute().data or []
    return sorted({str(row.get("report_date"))[:10] for row in rows if row.get("report_date")}, reverse=True)


def _to_number(value):
    if value in (None, ""):
        return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def _safe_ratio(numerator, denominator):
    if denominator:
        return numerator / denominator
    return pd.NA


def _ads_cluster_report_columns():
    return ["Кластер", "CTR", "CPO Корзины", "CPO Заказов", "Показы", "CPC", "Затраты", "Заказы", "Корзина"]


def build_ads_clusters_report(rows, cluster_filter="", min_orders_filter=10):
    aggregated = {}
    for row in rows:
        cluster = str(row.get("cluster") or "").strip()
        if not cluster:
            continue
        item = aggregated.setdefault(
            cluster,
            {
                "Кластер": cluster,
                "Показы": 0.0,
                "Клики": 0.0,
                "Затраты": 0.0,
                "Заказы": 0.0,
                "Корзина": 0.0,
            },
        )
        item["Показы"] += _to_number(row.get("impressions"))
        item["Клики"] += _to_number(row.get("clicks"))
        item["Затраты"] += _to_number(row.get("spend"))
        item["Заказы"] += _to_number(row.get("orders_count"))
        item["Корзина"] += _to_number(row.get("cart_count"))

    report_rows = []
    text_filter = str(cluster_filter or "").strip().lower()
    for item in aggregated.values():
        cluster_lower = item["Кластер"].lower()
        if text_filter and text_filter not in cluster_lower:
            continue
        if min_orders_filter > 0 and item["Заказы"] < min_orders_filter:
            continue

        impressions = item["Показы"]
        clicks = item["Клики"]
        spend = item["Затраты"]
        carts = item["Корзина"]
        orders = item["Заказы"]
        report_rows.append(
            {
                "Кластер": item["Кластер"],
                "CTR": _safe_ratio(clicks * 100, impressions),
                "CPO Корзины": _safe_ratio(spend, carts),
                "CPO Заказов": _safe_ratio(spend, orders),
                "Показы": int(impressions),
                "CPC": _safe_ratio(spend, clicks),
                "Затраты": spend,
                "Заказы": int(orders),
                "Корзина": int(carts),
                "_clicks": clicks,
            }
        )

    report_rows = sorted(
        report_rows,
        key=lambda row: (row["Заказы"], row["Затраты"], row["Показы"]),
        reverse=True,
    )
    if not report_rows:
        return pd.DataFrame(columns=_ads_cluster_report_columns())

    total_impressions = sum(row["Показы"] for row in report_rows)
    total_clicks = sum(row["_clicks"] for row in report_rows)
    total_spend = sum(row["Затраты"] for row in report_rows)
    total_orders = sum(row["Заказы"] for row in report_rows)
    total_carts = sum(row["Корзина"] for row in report_rows)
    report_rows.append(
        {
            "Кластер": "Итого",
            "CTR": _safe_ratio(total_clicks * 100, total_impressions),
            "CPO Корзины": _safe_ratio(total_spend, total_carts),
            "CPO Заказов": _safe_ratio(total_spend, total_orders),
            "Показы": int(total_impressions),
            "CPC": _safe_ratio(total_spend, total_clicks),
            "Затраты": total_spend,
            "Заказы": int(total_orders),
            "Корзина": int(total_carts),
            "_clicks": total_clicks,
        }
    )
    return pd.DataFrame(report_rows).drop(columns=["_clicks"])[_ads_cluster_report_columns()]


def _format_ads_cluster_value(value, suffix="", decimals=2):
    if pd.isna(value):
        return "—"
    if decimals == 0:
        formatted_value = format_number(int(value))
    else:
        formatted_value = f"{float(value):,.{decimals}f}".replace(",", " ")
    return f"{formatted_value}{suffix}"


def prepare_ads_clusters_report_for_display(report):
    display_report = report.copy()
    money_columns = ["CPO Корзины", "CPO Заказов", "CPC", "Затраты"]
    count_columns = ["Показы", "Заказы", "Корзина"]

    display_report["CTR"] = display_report["CTR"].apply(
        lambda value: _format_ads_cluster_value(value, suffix="%")
    )
    for column in money_columns:
        display_report[column] = display_report[column].apply(
            lambda value: _format_ads_cluster_value(value, suffix=" ₽")
        )
    for column in count_columns:
        display_report[column] = display_report[column].apply(
            lambda value: _format_ads_cluster_value(value, decimals=0)
        )
    return display_report


def count_ads_cluster_rows_after_text_filter(rows, cluster_filter=""):
    clusters = {
        str(row.get("cluster") or "").strip()
        for row in rows
        if str(row.get("cluster") or "").strip()
    }
    text_filter = str(cluster_filter or "").strip().lower()
    return sum(1 for cluster in clusters if not text_filter or text_filter in cluster.lower())


def ads_cluster_orders_filter_debug(rows, cluster_filter="", min_orders_filter=10):
    aggregated = {}
    for row in rows:
        cluster = str(row.get("cluster") or "").strip()
        if not cluster:
            continue
        aggregated[cluster] = aggregated.get(cluster, 0) + _to_number(row.get("orders_count"))

    text_filter = str(cluster_filter or "").strip().lower()
    orders_counts = [
        orders_count
        for cluster, orders_count in aggregated.items()
        if not text_filter or text_filter in cluster.lower()
    ]
    rows_after_text = len(orders_counts)
    if min_orders_filter > 0:
        rows_after_orders_filter = sum(
            1 for orders_count in orders_counts if orders_count >= min_orders_filter
        )
        rows_final = rows_after_orders_filter
    else:
        rows_after_orders_filter = "not_applied"
        rows_final = rows_after_text

    return {
        "min_orders_filter": min_orders_filter,
        "rows_before_orders_filter": rows_after_text,
        "rows_after_orders_filter": rows_after_orders_filter,
        "max_orders_count": max(orders_counts) if orders_counts else 0,
        "rows_final": rows_final,
    }


def has_seller_id(row):
    return str(row.get("seller_id") or row.get("sellerId") or "").strip() != ""


st.set_page_config(page_title="Morning Brief — Панель управления WB", layout="wide")
try:
    credentials_info = get_supabase_credentials_info()
    connection_diagnostics = check_dashboard_connection()
    sellers, sellers_by_id = fetch_sellers()
    report_dates, problem_date_field = fetch_report_dates()
    report_dates = [
        row["business_date"]
        for row in align_time_series(
            [{problem_date_field: value} for value in report_dates],
            date_column=problem_date_field,
        )
        if row.get("business_date")
    ]
    report_dates = sorted(set(report_dates), reverse=True)
except Exception as error:
    st.error(f"Не удалось подключиться к Supabase: {error}")
    st.stop()

try:
    get_supabase_client().table("problems").select("id").limit(1).execute()
    problems_access_status = "OK"
    problems_access_error = ""
except Exception as error:
    problems_access_status = "FAILED"
    problems_access_error = str(error)

query_nm_id = st.query_params.get("nm_id")
query_seller_id = st.query_params.get("seller_id")
query_mode = st.query_params.get("mode")
dashboard_modes = ["Executive Dashboard", "Карточка SKU"]
default_mode = "Карточка SKU" if query_nm_id else "Executive Dashboard"
initial_dashboard_mode = query_mode if query_mode in dashboard_modes else default_mode

with st.sidebar:
    dashboard_mode = st.selectbox(
        "Режим Dashboard",
        dashboard_modes,
        index=dashboard_modes.index(initial_dashboard_mode),
    )

should_render_sku = (
    bool(query_nm_id)
    or query_mode == "Карточка SKU"
    or (query_mode != "Executive Dashboard" and dashboard_mode == "Карточка SKU")
)

if should_render_sku:
    render_sku_page(sellers, sellers_by_id, initial_nm_id=query_nm_id, selected_seller=query_seller_id)
    st.stop()

if query_mode == "Executive Dashboard":
    del st.query_params["mode"]

st.title("Панель управления WB")
st.caption("Ежедневная аналитика Wildberries: потери, продавцы, товары и причины просадок")

with st.sidebar:
    st.header("Фильтры")
    if report_dates:
        selected_date_raw = st.selectbox("Дата отчёта", report_dates, index=0)
        selected_date = normalize_report_date(selected_date_raw)
        report_date = selected_date.isoformat() if selected_date else None
    else:
        selected_date_raw = None
        selected_date = None
        report_date = None
        st.warning("Даты отчётов не найдены в problems.")

    seller_options = ["Все продавцы", *[str(row.get("seller_id") or row.get("id")) for row in sellers if row.get("seller_id") or row.get("id")]]
    seller_labels = {"Все продавцы": "Все продавцы"}
    seller_labels.update({key: sellers_by_id.get(key, key) for key in seller_options if key != "Все продавцы"})
    selected_seller = st.selectbox(
        "Продавец",
        seller_options,
        format_func=lambda value: seller_labels.get(value, value),
    )

date_problems = []
if report_date:
    date_problems = fetch_problems(
        report_date=report_date,
        limit=1,
        date_field=problem_date_field,
    )

fallback_message = ""
if report_date and not date_problems:
    fallback_report_date = closest_available_date(report_dates, report_date, max_shift_days=3)
    if fallback_report_date and fallback_report_date != report_date:
        st.warning(f"FALLBACK_USED: shifted to closest available date {fallback_report_date}")
        fallback_message = f"FALLBACK_USED: shifted to closest available date {fallback_report_date}"
        report_date = fallback_report_date
        selected_date = normalize_report_date(report_date)
        date_problems = fetch_problems(
            report_date=report_date,
            limit=1,
            date_field=problem_date_field,
        )

unfiltered_problems = fetch_problems(
    report_date=report_date,
    seller_id=selected_seller,
    date_field=problem_date_field,
)
all_date_filter_rows = fetch_problems(
    report_date=None,
    seller_id=selected_seller,
    date_field=problem_date_field,
)
empty_data_debug = debug_date_filter(
    all_date_filter_rows,
    report_date,
    date_field=problem_date_field,
    filtered_count=len(unfiltered_problems),
)
if fallback_message:
    empty_data_debug["fallback"] = fallback_message
with st.sidebar:
    selected_reason = st.selectbox(
        "Причина проблемы",
        REASON_FILTER_OPTIONS,
        help=(
            "Выберите управленческую группу причин. Технические метрики объединены "
            "в понятные категории: конверсия, реклама, остатки, заказы, выручка."
        ),
    )
    if selected_reason != "Все причины":
        st.caption(f"Пояснение: {reason_explanation(selected_reason)}")

    with st.expander("Отчёт по кластерам рекламы", expanded=False):
        cluster_sellers = fetch_ads_cluster_sellers()
        seller_report_options = ["", *[row["seller_id"] for row in cluster_sellers]]
        seller_report_labels = {"": "Выберите продавца"}
        seller_report_labels.update({row["seller_id"]: row["seller_name"] for row in cluster_sellers})
        selected_cluster_seller = st.selectbox(
            "продавец",
            seller_report_options,
            format_func=lambda value: seller_report_labels.get(value, value),
            key="ads_cluster_seller_id",
        )

        seller_available_dates = fetch_ads_cluster_available_dates(selected_cluster_seller)
        cluster_default_date = (
            normalize_report_date(seller_available_dates[0])
            if seller_available_dates
            else date.today()
        )
        for key in ("ads_cluster_start_date", "ads_cluster_end_date"):
            selected_date = st.session_state.get(key)
            selected_date_iso = selected_date.isoformat() if hasattr(selected_date, "isoformat") else str(selected_date or "")
            if not selected_date or (seller_available_dates and selected_date_iso not in seller_available_dates):
                st.session_state[key] = cluster_default_date

        cluster_start_date = st.date_input("дата начала", key="ads_cluster_start_date")
        cluster_end_date = st.date_input("дата окончания", key="ads_cluster_end_date")

        manual_campaign_id_raw = st.text_input(
            "ID рекламной кампании",
            placeholder="37030841",
            key="ads_cluster_campaign_search",
            disabled=not selected_cluster_seller,
        )
        campaign_search = manual_campaign_id_raw.strip()
        manual_campaign_id_parsed = None
        if campaign_search:
            if campaign_search.isdigit():
                manual_campaign_id_parsed = int(campaign_search)
            else:
                st.error("ID кампании должен быть числом")
        found_cluster_campaign = (
            find_ads_cluster_campaign(selected_cluster_seller, campaign_search)
            if manual_campaign_id_parsed is not None
            else None
        )
        if campaign_search and found_cluster_campaign:
            if found_cluster_campaign.get("data_source") == "ads_clusters_daily":
                st.success(
                    "Кампания найдена:\n\n"
                    f"{found_cluster_campaign['campaign_id']}\n\n"
                    f"{_campaign_type_display_name(found_cluster_campaign['campaign_type'])}\n\n"
                    f"{found_cluster_campaign.get('seller_name') or seller_report_labels.get(selected_cluster_seller, '')}"
                )
            else:
                st.warning(
                    "Кампания найдена в рекламной статистике, но кластеры по ней ещё не собраны. "
                    "Запустите сбор кластеров или добавьте campaign_id в ADS_CLUSTER_FORCE_CAMPAIGN_IDS."
                )
        elif campaign_search and manual_campaign_id_parsed is not None:
            st.warning("Кампания не найдена в сохранённых данных")

        campaign_options, campaign_list_debug = fetch_ads_cluster_campaigns(
            selected_cluster_seller,
            cluster_start_date.isoformat(),
            cluster_end_date.isoformat(),
        )
        campaign_ids = ["", *[row["campaign_id"] for row in campaign_options]]
        campaign_labels = {"": "Выберите кампанию"}
        campaign_labels.update(
            {row["campaign_id"]: row["display_name"] for row in campaign_options}
        )
        campaign_debug_by_id = {row["campaign_id"]: row for row in campaign_options}
        if st.session_state.get("ads_cluster_campaign_id") not in campaign_ids:
            st.session_state["ads_cluster_campaign_id"] = ""
        selected_cluster_campaign = st.selectbox(
            "рекламная кампания",
            campaign_ids,
            format_func=lambda value: campaign_labels.get(value, value),
            key="ads_cluster_campaign_id",
            disabled=not selected_cluster_seller or bool(campaign_search),
        )
        campaign_id_source = "manual_input" if campaign_search else "selectbox"
        effective_cluster_campaign = (
            manual_campaign_id_parsed
            if campaign_id_source == "manual_input"
            else selected_cluster_campaign
        )

        cluster_available_dates = fetch_ads_cluster_available_dates(
            selected_cluster_seller, effective_cluster_campaign
        )
        cluster_text_filter = st.text_input("текстовый фильтр по кластеру", key="ads_cluster_text_filter")
        min_orders_filter = st.number_input(
            "Минимум заказов",
            min_value=0,
            value=10,
            step=1,
            key="ads_cluster_min_orders_filter",
        )
        show_cluster_report = st.button("Показать отчёт", key="ads_cluster_show_report")

        if show_cluster_report and campaign_search and manual_campaign_id_parsed is None:
            st.error("ID кампании должен быть числом")
        elif show_cluster_report:
            selected_campaign_debug = (
                found_cluster_campaign
                if campaign_id_source == "manual_input"
                else campaign_debug_by_id.get(effective_cluster_campaign, {})
            )
            if not isinstance(selected_campaign_debug, dict):
                st.warning("Кампания не найдена в сохранённых данных")
                selected_campaign_debug = {}
            st.session_state["ads_cluster_report_request"] = {
                "seller_id": selected_cluster_seller,
                "campaign_id": effective_cluster_campaign,
                "manual_campaign_id_raw": manual_campaign_id_raw,
                "manual_campaign_id_parsed": manual_campaign_id_parsed,
                "campaign_id_source": campaign_id_source,
                "campaign_type": selected_campaign_debug.get("campaign_type", ""),
                "display_name": selected_campaign_debug.get("display_name", ""),
                "start_date": cluster_start_date.isoformat(),
                "end_date": cluster_end_date.isoformat(),
                "cluster_filter": cluster_text_filter,
                "min_orders_filter": min_orders_filter,
                "seller_name": seller_report_labels.get(selected_cluster_seller, ""),
                "campaign_name": selected_campaign_debug.get("campaign_name", ""),
                "available_campaign_ids": [row["campaign_id"] for row in campaign_options],
                "available_dates": cluster_available_dates,
                "campaigns_source": campaign_list_debug.get("campaigns_source", ""),
                "campaigns_found": campaign_list_debug.get("campaigns_found", len(campaign_options)),
                "campaigns_after_filter": len(campaign_ids) - 1,
                "campaign_list_source": campaign_list_debug.get("campaign_list_source", ""),
                "campaigns_loaded": campaign_list_debug.get("campaigns_loaded", 0),
                "campaign_ids_loaded": campaign_list_debug.get("campaign_ids_loaded", []),
            }

    with st.expander("➕ Добавить изменение", expanded=False):
        st.caption("Быстрое ручное внесение изменения в change_log.")
        change_log_available = is_change_log_available()
        if not change_log_available:
            st.warning("Журнал изменений недоступен. Проверьте таблицу change_log.")
        change_nm_id = st.text_input("Артикул WB", key="change_log_nm_id").strip()
        selected_product = None
        products_available = True
        if change_nm_id:
            selected_product, products_available = find_product_by_nm_id(
                change_nm_id,
                seller_id=selected_seller if selected_seller != "Все продавцы" else None,
            )
            if selected_product:
                st.success("Товар найден в products.")
                st.write(f"**Название:** {selected_product.get('title') or '—'}")
                st.write(
                    "**Продавец:** "
                    f"{selected_product.get('seller_name') or sellers_by_id.get(str(selected_product.get('seller_id')), '') or selected_product.get('seller_id') or '—'}"
                )
                st.write(f"**Артикул продавца:** {selected_product.get('vendor_code') or '—'}")
            elif products_available:
                st.warning(
                    "Артикул не найден в products. Изменение можно сохранить, "
                    "но seller_id и vendor_code могут быть пустыми."
                )
            else:
                st.warning("Не удалось проверить артикул в products. Изменение можно сохранить вручную.")

        with st.form("change_log_quick_form", clear_on_submit=True):
            change_type = st.selectbox(
                "Тип изменения",
                [
                    "фото",
                    "инфографика",
                    "SEO",
                    "цена",
                    "скидка",
                    "реклама",
                    "ставка",
                    "остатки",
                    "поставка",
                    "акция",
                    "описание",
                    "характеристики",
                    "другое",
                ],
            )
            change_date = st.date_input("Дата изменения", value=date.today())
            old_value = st.text_area("Было", height=80)
            new_value = st.text_area("Стало", height=80)
            changed_by = st.text_input("Кто изменил")
            comment = st.text_area("Комментарий", height=80)
            submitted_change = st.form_submit_button("Сохранить изменение")

        if submitted_change:
            if not change_log_available:
                st.warning("Журнал изменений недоступен. Проверьте таблицу change_log.")
            elif not change_nm_id:
                st.warning("Введите артикул WB.")
            else:
                product = selected_product
                if product is None:
                    product, _ = find_product_by_nm_id(
                        change_nm_id,
                        seller_id=selected_seller if selected_seller != "Все продавцы" else None,
                    )
                product = product or {}
                product_seller_id = product.get("seller_id")
                product_seller_name = product.get("seller_name") or sellers_by_id.get(str(product_seller_id), "")
                try:
                    change_nm_id_for_insert = int(str(change_nm_id).strip())
                except ValueError as error:
                    st.error(
                        "Не удалось сохранить изменение в change_log.\n"
                        "Ошибка Supabase:\n"
                        f"Артикул WB должен быть числом: {error}"
                    )
                else:
                    change_log_payload = {
                        "change_date": str(change_date),
                        "seller_id": product_seller_id or None,
                        "seller_name": product_seller_name or None,
                        "nm_id": change_nm_id_for_insert,
                        "vendor_code": product.get("vendor_code") or None,
                        "change_type": change_type,
                        "old_value": old_value or "",
                        "new_value": new_value or "",
                        "change_source": "manual_dashboard",
                        "changed_by": changed_by or "",
                        "comment": comment or "",
                    }
                    try:
                        get_supabase_client().table("change_log").insert(change_log_payload).execute()
                    except Exception as error:
                        st.error(
                            "Не удалось сохранить изменение в change_log.\n"
                            "Ошибка Supabase:\n"
                            f"{error}"
                        )
                    else:
                        st.cache_data.clear()
                        st.success("Изменение сохранено в change_log.")

    show_rows_without_seller_id = st.checkbox("Показывать строки без seller_id", value=False)
    show_debug = st.checkbox("Показать debug", value=False)

ads_cluster_request = st.session_state.get("ads_cluster_report_request")
if ads_cluster_request:
    st.subheader("Отчёт по кластерам рекламы")
    if not ads_cluster_request.get("seller_id") or not ads_cluster_request.get("campaign_id"):
        st.warning("Выберите продавца и рекламную кампанию для отчёта по кластерам рекламы.")
    elif ads_cluster_request["start_date"] > ads_cluster_request["end_date"]:
        st.warning("Дата начала не должна быть позже даты окончания.")
    else:
        ads_cluster_rows, ads_cluster_debug = fetch_ads_cluster_rows(
            ads_cluster_request["seller_id"],
            ads_cluster_request["campaign_id"],
            ads_cluster_request["start_date"],
            ads_cluster_request["end_date"],
        )
        ads_cluster_report = build_ads_clusters_report(
            ads_cluster_rows,
            cluster_filter=ads_cluster_request.get("cluster_filter", ""),
            min_orders_filter=ads_cluster_request.get("min_orders_filter", 10),
        )
        orders_filter_debug = ads_cluster_orders_filter_debug(
            ads_cluster_rows,
            ads_cluster_request.get("cluster_filter", ""),
            ads_cluster_request.get("min_orders_filter", 10),
        )
        ads_cluster_debug.update(
            {
                "selected_campaign_name": ads_cluster_request.get("campaign_name")
                or ads_cluster_debug.get("selected_campaign_name", ""),
                "rows_after_text_filter": count_ads_cluster_rows_after_text_filter(
                    ads_cluster_rows,
                    ads_cluster_request.get("cluster_filter", ""),
                ),
                **orders_filter_debug,
                "manual_campaign_id_raw": ads_cluster_request.get("manual_campaign_id_raw", ""),
                "manual_campaign_id_parsed": ads_cluster_request.get("manual_campaign_id_parsed"),
                "campaign_id_source": ads_cluster_request.get("campaign_id_source", ""),
                "campaigns_source": ads_cluster_request.get("campaigns_source", ""),
                "campaigns_found": ads_cluster_request.get("campaigns_found", 0),
                "campaign_list_source": ads_cluster_request.get("campaign_list_source", ""),
                "campaigns_loaded": ads_cluster_request.get("campaigns_loaded", 0),
                "campaign_ids_loaded": ads_cluster_request.get("campaign_ids_loaded", []),
            }
        )
        logger.info("DEBUG ADS CLUSTERS final %s", ads_cluster_debug)

        if ads_cluster_report.empty:
            if (
                ads_cluster_request.get("min_orders_filter", 10) > 0
                and ads_cluster_debug["rows_before_orders_filter"] > 0
                and ads_cluster_debug["rows_after_orders_filter"] == 0
            ):
                st.info(
                    "По выбранной кампании есть кластеры, но нет кластеров с нужным минимумом заказов. "
                    "Уменьшите значение в поле «Минимум заказов»."
                )
            elif (
                ads_cluster_debug.get("campaign_id_source") == "manual_input"
                and ads_cluster_debug.get("rows_after_campaign_filter") == 0
                and ads_cluster_debug.get("exists_in_daily_ads_metrics")
            ):
                st.info(
                    "Кампания найдена в рекламной статистике, но кластеры по ней ещё не собраны. "
                    "Запустите сбор кластеров или добавьте campaign_id в ADS_CLUSTER_FORCE_CAMPAIGN_IDS."
                )
            elif (
                ads_cluster_debug.get("campaign_id_source") == "manual_input"
                and ads_cluster_debug.get("rows_after_campaign_filter") == 0
                and not ads_cluster_debug.get("exists_in_daily_ads_metrics")
            ):
                st.info("Кампания не найдена в сохранённых данных.")
            else:
                st.info("По выбранной кампании и периоду кластеров не найдено.")
        else:
            st.dataframe(
                prepare_ads_clusters_report_for_display(ads_cluster_report),
                width="stretch",
                hide_index=True,
            )
        if show_debug:
            with st.expander("Debug ADS Clusters", expanded=False):
                st.markdown("**DEBUG ADS CLUSTERS**")
                st.write(f"selected_seller_id:\n\n{ads_cluster_debug['selected_seller_id']}")
                st.write(f"campaign_id:\n\n{ads_cluster_debug['selected_campaign_id']}")
                st.write(f"manual_campaign_id_raw:\n\n{ads_cluster_debug.get('manual_campaign_id_raw', '')}")
                st.write(f"manual_campaign_id_parsed:\n\n{ads_cluster_debug.get('manual_campaign_id_parsed')}")
                st.write(f"campaign_id_source:\n\n{ads_cluster_debug.get('campaign_id_source', '')}")
                st.write(f"selected_campaign_name:\n\n{ads_cluster_debug['selected_campaign_name']}")
                st.write(f"selected_start_date:\n\n{ads_cluster_debug['selected_start_date']}")
                st.write(f"selected_end_date:\n\n{ads_cluster_debug['selected_end_date']}")
                st.write(f"campaigns_source:\n\n{ads_cluster_debug.get('campaigns_source', '')}")
                st.write(f"campaigns_found:\n\n{ads_cluster_debug.get('campaigns_found', 0)}")
                st.write(f"campaign_ids_loaded:\n\n{ads_cluster_debug.get('campaign_ids_loaded', [])}")
                st.write(f"exists_in_daily_ads_metrics:\n\n{ads_cluster_debug.get('exists_in_daily_ads_metrics')}")
                st.write(f"exists_in_ads_clusters_daily:\n\n{ads_cluster_debug.get('exists_in_ads_clusters_daily')}")
                st.write(
                    f"available_cluster_campaign_ids:\n\n{ads_cluster_debug.get('available_cluster_campaign_ids', [])}"
                )
                st.write(f"rows_loaded:\n\n{ads_cluster_debug['rows_loaded_from_supabase']}")
                st.write(f"rows_after_seller:\n\n{ads_cluster_debug['rows_after_seller_filter']}")
                st.write(f"rows_after_campaign:\n\n{ads_cluster_debug['rows_after_campaign_filter']}")
                st.write(f"rows_after_date:\n\n{ads_cluster_debug['rows_after_date_filter']}")
                st.write(f"rows_after_text:\n\n{ads_cluster_debug['rows_after_text_filter']}")
                st.write(f"min_orders_filter:\n\n{ads_cluster_debug['min_orders_filter']}")
                st.write(f"rows_before_orders_filter:\n\n{ads_cluster_debug['rows_before_orders_filter']}")
                st.write(f"rows_after_orders_filter:\n\n{ads_cluster_debug['rows_after_orders_filter']}")
                st.write(f"max_orders_count:\n\n{ads_cluster_debug['max_orders_count']}")
                st.write(f"rows_final:\n\n{ads_cluster_debug['rows_final']}")
                st.json(ads_cluster_debug)

excluded_problems_without_seller_id = 0
if show_rows_without_seller_id:
    dashboard_problems = unfiltered_problems
else:
    dashboard_problems = [row for row in unfiltered_problems if has_seller_id(row)]
    excluded_problems_without_seller_id = len(unfiltered_problems) - len(dashboard_problems)

problems = [row for row in dashboard_problems if matches_reason_filter(row, selected_reason)]
if date_problems and not problems:
    empty_data_debug["reason"] = "FILTER_EXCLUDED_ALL_ROWS"
    empty_data_debug["rows_before_filter"] = len(unfiltered_problems)
    empty_data_debug["rows_after_filter"] = len(problems)
    empty_data_debug["filter_reason"] = {
        "selected_reason": selected_reason,
        "show_rows_without_seller_id": show_rows_without_seller_id,
        "excluded_problems_without_seller_id": excluded_problems_without_seller_id,
    }
problems_diagnostics = fetch_problems_diagnostics(
    report_date=report_date,
    date_field=problem_date_field,
    available_dates=report_dates,
)
quality = fetch_data_quality(report_date=report_date)

if not connection_diagnostics["problems_readable"] or (
    connection_diagnostics["problems_total_count"] == 0 and not date_problems
):
    st.warning("Dashboard не видит данные problems. Проверьте Supabase key и RLS policies.")

critical_sellers = len({row.get("seller_id") for row in problems if row.get("seller_id")})
critical_sku = len({row.get("nm_id") or row.get("nmId") for row in problems if row.get("nm_id") or row.get("nmId")})
reason_summaries = reason_loss_summary(problems)
sum_reason_lost_revenue = sum(summary["lost_revenue"] for summary in reason_summaries)
sum_reason_lost_orders = sum(summary["lost_orders"] for summary in reason_summaries)
total_day_lost_revenue = sum_reason_lost_revenue
total_day_lost_orders = sum_reason_lost_orders
reason_revenue_diff = sum_reason_lost_revenue - total_day_lost_revenue
top_reason = reason_summaries[0] if reason_summaries else None
reason = top_reason["reason"] if top_reason else main_reason(problems)
reason_loss_label = "Потеря"
reason_loss_value = "0 ₽"
if top_reason and top_reason["metric_key"] == "lost_orders":
    reason_loss_label = "Потеря заказов"
    reason_loss_value = format_number(round(top_reason["lost_orders"]))
elif top_reason:
    reason_loss_value = format_money(top_reason["lost_revenue"])

card_1, card_2, card_3, card_4, card_5 = st.columns(5)
card_1.metric("Потеря выручки за день ⓘ", format_money(total_day_lost_revenue), help=LOST_REVENUE_HELP)
card_2.metric("Потеря заказов за день ⓘ", format_number(round(total_day_lost_orders)), help=LOST_ORDERS_HELP)
card_3.metric("Критичные продавцы", format_number(critical_sellers))
card_4.metric("Критичные SKU", format_number(critical_sku))
card_5.metric("Главная причина ⓘ", reason, help=main_reason_help(reason))
if top_reason:
    st.caption(
        f"Причина: {reason} · {reason_loss_label}: {reason_loss_value} · "
        f"SKU: {format_number(top_reason['sku_count'])} · "
        f"Доля потерь: {format_number(round(top_reason['share']))}%"
    )
    st.caption("Описание причины: " + reason_explanation(reason), help=REASON_DESCRIPTION_HELP)
    st.markdown(f"**Потери по причинам:** {help_icon(REASON_LOSS_TOOLTIP)}", unsafe_allow_html=True)
    for reason_summary in reason_summaries:
        loss_value = (
            format_money(reason_summary["lost_revenue"])
            if reason_summary["metric_key"] == "lost_revenue"
            else f"{format_number(round(reason_summary['lost_orders']))} заказов"
        )
        st.markdown(
            f"{reason_summary['reason'].capitalize()} — "
            f"{loss_value} ({format_number(round(reason_summary['share']))}%) "
            f"{help_icon(reason_loss_help(reason_summary))}",
            unsafe_allow_html=True,
        )
else:
    st.caption(f"Описание причины: {reason_explanation(reason)}", help=REASON_DESCRIPTION_HELP)

st.subheader(
    "Что смотреть первым",
    help=FIRST_LOOK_HELP,
)
if problems:
    top_problem = max(problems, key=lost_revenue)
    top_seller = sellers_by_id.get(str(top_problem.get("seller_id")), top_problem.get("seller_id") or "Без seller_id")
    st.info(
        f"Начните с продавца {top_seller}, SKU {top_problem.get('nm_id') or top_problem.get('nmId')}: "
        f"потеря {format_money(lost_revenue(top_problem))} ⓘ, причина — {main_reason([top_problem])} ⓘ."
    )
elif not report_dates:
    st.warning("Даты отчётов не найдены в problems.")
elif not date_problems:
    debug_reason = empty_data_debug.get("reason") or "NO_DATA_FOR_SELECTED_DATE"
    st.warning(f"По выбранной дате данные не найдены. Причина: {debug_reason}")
    with st.expander("Техническое объяснение", expanded=False):
        st.json(empty_data_debug)
else:
    st.success("По выбранным фильтрам критичных проблем не найдено.")
    with st.expander("Техническое объяснение", expanded=False):
        st.json(empty_data_debug)

st.subheader("Продавцы")
seller_table = dataframe_for_display(prepare_seller_table(problems, sellers_by_id))
st.dataframe(seller_table, width="stretch", hide_index=True)

st.subheader("Сводка проблем по SKU")
sku_table = dataframe_for_display(prepare_sku_table(problems, sellers_by_id).head(100))
if not sku_table.empty:
    sku_table = sku_table.copy()
    sku_table.insert(
        0,
        "открыть",
        sku_table["артикул WB"].apply(lambda nm_id: f"?nm_id={quote(str(nm_id))}" if nm_id else ""),
    )
st.dataframe(
    sku_table,
    width="stretch",
    hide_index=True,
    column_config={
        "открыть": st.column_config.LinkColumn("Карточка", display_text="Открыть"),
        "потеря выручки": st.column_config.NumberColumn(
            "Потеря выручки ⓘ",
            help=LOST_REVENUE_HELP,
        ),
        "потеря заказов": st.column_config.NumberColumn(
            "Потеря заказов ⓘ",
            help=LOST_ORDERS_HELP,
        ),
        "главная причина": st.column_config.TextColumn(
            "Главная причина ⓘ",
            help=MAIN_REASON_TOOLTIP,
        ),
        "главное подтверждение": st.column_config.TextColumn(
            "Главное подтверждение ⓘ",
            help=CONFIRMATION_COLUMN_HELP + "\n\n" + MAIN_REASON_TOOLTIP,
        ),
        "подсказка подтверждения": st.column_config.TextColumn(
            "Подсказка по подтверждению",
            help="Поясняет, какие данные подтвердили причину проблемы по SKU.",
        ),
        "пояснение причины": st.column_config.TextColumn(
            "Описание причины ⓘ",
            help=REASON_DESCRIPTION_HELP,
        ),
    },
)

st.subheader("Качество данных")
quality_1, quality_2, quality_3, quality_4 = st.columns(4)
quality_1.metric("problems без seller_id", format_number(quality["problems_without_seller_id"]))
quality_2.metric("ads_bid_history без seller_id", format_number(quality["ads_bid_history_without_seller_id"]))
quality_3.metric("SKU без рекламы", format_number(quality["sku_without_ads"]))
quality_4.metric("SKU без поставок", format_number(quality["sku_without_supplies"]))


if show_debug:
    with st.sidebar:
        st.divider()
        st.subheader("Dashboard debug")
        supabase_status = "OK" if problems_diagnostics["supabase_connected"] else "ERROR"
        problems_status = "OK" if problems_diagnostics["problems_readable"] else "ERROR"
        last_error = problems_diagnostics["last_query_error"] or "—"
        st.caption("Dashboard connection:")
        st.caption(f"Supabase: {supabase_status}")
        st.caption(f"problems readable: {problems_status}")
        st.caption(f"problems total count: {problems_diagnostics['problems_total_count']}")
        st.caption(f"date field used: {problems_diagnostics['date_field_used']}")
        st.caption(f"available dates count: {problems_diagnostics['available_dates_count']}")
        st.caption(f"available dates list: {problems_diagnostics['available_dates_sample']}")
        st.caption(f"selected date: {problems_diagnostics['selected_date'] or '—'}")
        st.caption(f"date debug reason: {empty_data_debug.get('reason') or '—'}")
        st.caption(f"source min/max report_date: {empty_data_debug.get('min_report_date') or '—'} / {empty_data_debug.get('max_report_date') or '—'}")
        st.caption(f"rows before date filter: {empty_data_debug.get('rows_before_filter')}")
        st.caption(f"rows after date filter: {empty_data_debug.get('rows_after_filter')}")
        st.caption(f"report_date dtype: {empty_data_debug.get('report_date_dtype')}")
        st.caption(f"rows in ±3 days: {empty_data_debug.get('rows_in_plus_minus_3_days')}")
        if empty_data_debug.get("fallback"):
            st.caption(empty_data_debug["fallback"])
        st.json(empty_data_debug)
        if problem_date_field == "report_date":
            st.caption("report_date берётся из сохранённых problems.")
        st.caption(f"last error: {last_error}")
        st.caption(f"Supabase URL: {credentials_info['supabase_url'] or '—'}")
        st.caption(f"KEY TYPE: {credentials_info['key_type']}")
        st.caption("Problems table access test")
        if problems_access_error:
            st.caption(f"Problems access: {problems_access_status} — {problems_access_error}")
        else:
            st.caption(f"Problems access: {problems_access_status}")
        st.caption(f"excluded problems without seller_id: {excluded_problems_without_seller_id}")
        st.caption(f"credentials source: {credentials_info['credentials_source']}")
        st.caption("Потери по причинам:")
        st.caption(f"общая потеря = {format_money(total_day_lost_revenue)}")
        st.caption(f"сумма причин = {format_money(sum_reason_lost_revenue)}")
        st.caption(f"расхождение = {format_money(reason_revenue_diff)}")
