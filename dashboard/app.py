"""Streamlit Executive Dashboard for Morning Brief."""

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

import streamlit as st

from core import date_engine

align_time_series = date_engine.align_time_series
normalize_report_date = date_engine.normalize_report_date
to_business_date = date_engine.to_business_date


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
