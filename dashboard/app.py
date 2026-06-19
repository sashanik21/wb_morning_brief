"""Streamlit Executive Dashboard for Morning Brief."""

import sys
from pathlib import Path

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent

for path in (str(CURRENT_DIR), str(PROJECT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import streamlit as st

from formatters import (
    format_money,
    format_number,
    lost_orders,
    lost_revenue,
    main_reason,
    matches_reason_filter,
    reason_explanation,
    reason_loss_summary,
    REASON_FILTER_OPTIONS,
    prepare_seller_table,
    prepare_sku_table,
)
from dashboard_queries import (
    check_dashboard_connection,
    dataframe_for_display,
    fetch_data_quality,
    fetch_problems,
    fetch_problems_diagnostics,
    fetch_report_dates,
    fetch_sellers,
)
from supabase_client import get_supabase_client, get_supabase_credentials_info
from sku_page import render_sku_page


def has_seller_id(row):
    return str(row.get("seller_id") or row.get("sellerId") or "").strip() != ""


st.set_page_config(page_title="Morning Brief — Панель управления WB", layout="wide")
try:
    credentials_info = get_supabase_credentials_info()
    connection_diagnostics = check_dashboard_connection()
    sellers, sellers_by_id = fetch_sellers()
    report_dates, problem_date_field = fetch_report_dates()
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

with st.sidebar:
    dashboard_mode = st.selectbox("Режим Dashboard", ["Executive Dashboard", "Карточка SKU"], index=0)

if dashboard_mode == "Карточка SKU":
    render_sku_page(sellers, sellers_by_id)
    st.stop()

st.title("Панель управления WB")
st.caption("Ежедневная аналитика Wildberries: потери, продавцы, товары и причины просадок")

with st.sidebar:
    st.header("Фильтры")
    if report_dates:
        report_date = st.selectbox("Дата отчёта", report_dates, index=0)
    else:
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
unfiltered_problems = fetch_problems(
    report_date=report_date,
    seller_id=selected_seller,
    date_field=problem_date_field,
)
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
    show_rows_without_seller_id = st.checkbox("Показывать строки без seller_id", value=False)
    show_debug = st.checkbox("Показать debug", value=False)

excluded_problems_without_seller_id = 0
if show_rows_without_seller_id:
    dashboard_problems = unfiltered_problems
else:
    dashboard_problems = [row for row in unfiltered_problems if has_seller_id(row)]
    excluded_problems_without_seller_id = len(unfiltered_problems) - len(dashboard_problems)

problems = [row for row in dashboard_problems if matches_reason_filter(row, selected_reason)]
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
card_1.metric("Потеря выручки за день", format_money(total_day_lost_revenue))
card_2.metric("Потеря заказов за день", format_number(round(total_day_lost_orders)))
card_3.metric("Критичные продавцы", format_number(critical_sellers))
card_4.metric("Критичные SKU", format_number(critical_sku))
card_5.metric("Главная причина просадок", reason, help=reason_explanation(reason))
if top_reason:
    st.caption(
        f"Причина: {reason} · {reason_loss_label}: {reason_loss_value} · "
        f"SKU: {format_number(top_reason['sku_count'])} · "
        f"Доля потерь: {format_number(round(top_reason['share']))}%"
    )
    st.caption("Что означает главная причина: " + reason_explanation(reason))
    st.markdown("**Потери по причинам:**")
    for reason_summary in reason_summaries:
        loss_value = (
            format_money(reason_summary["lost_revenue"])
            if reason_summary["metric_key"] == "lost_revenue"
            else f"{format_number(round(reason_summary['lost_orders']))} заказов"
        )
        st.caption(
            f"{reason_summary['reason'].capitalize()} — "
            f"{loss_value} ({format_number(round(reason_summary['share']))}%)"
        )
else:
    st.caption(f"Что означает главная причина: {reason_explanation(reason)}")

st.subheader("Что смотреть первым")
if problems:
    top_problem = max(problems, key=lost_revenue)
    top_seller = sellers_by_id.get(str(top_problem.get("seller_id")), top_problem.get("seller_id") or "Без seller_id")
    st.info(
        f"Начните с продавца {top_seller}, SKU {top_problem.get('nm_id') or top_problem.get('nmId')}: "
        f"потеря {format_money(lost_revenue(top_problem))}, причина — {main_reason([top_problem])}."
    )
elif not report_dates:
    st.warning("Даты отчётов не найдены в problems.")
elif not date_problems:
    st.warning("По выбранной дате данные не найдены.")
else:
    st.success("По выбранным фильтрам критичных проблем не найдено.")

st.subheader("Продавцы")
seller_table = dataframe_for_display(prepare_seller_table(problems, sellers_by_id))
st.dataframe(seller_table, width="stretch", hide_index=True)

st.subheader("TOP SKU")
sku_table = dataframe_for_display(prepare_sku_table(problems, sellers_by_id).head(100))
st.dataframe(sku_table, width="stretch", hide_index=True)

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
