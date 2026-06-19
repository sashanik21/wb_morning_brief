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
    prepare_seller_table,
    prepare_sku_table,
)
from dashboard_queries import (
    dataframe_for_display,
    fetch_data_quality,
    fetch_problems,
    fetch_problems_diagnostics,
    fetch_report_dates,
    fetch_sellers,
    unique_reasons,
)


st.set_page_config(page_title="Morning Brief — Executive Dashboard", layout="wide")
st.title("Executive Dashboard")
st.caption("Morning Brief: управленческая картина по потерям, продавцам и SKU")

try:
    sellers, sellers_by_id = fetch_sellers()
    report_dates, problem_date_field = fetch_report_dates()
except Exception as error:
    st.error(f"Не удалось подключиться к Supabase: {error}")
    st.stop()

with st.sidebar:
    st.header("Фильтры")
    report_date = st.selectbox("Дата отчёта", report_dates, index=0)

    seller_options = ["Все продавцы", *[str(row.get("seller_id") or row.get("id")) for row in sellers if row.get("seller_id") or row.get("id")]]
    seller_labels = {"Все продавцы": "Все продавцы"}
    seller_labels.update({key: sellers_by_id.get(key, key) for key in seller_options if key != "Все продавцы"})
    selected_seller = st.selectbox(
        "Продавец",
        seller_options,
        format_func=lambda value: seller_labels.get(value, value),
    )

date_problems = fetch_problems(
    report_date=report_date,
    limit=1,
    date_field=problem_date_field,
)
if not date_problems and report_dates:
    for fallback_report_date in report_dates:
        fallback_problems = fetch_problems(
            report_date=fallback_report_date,
            limit=1,
            date_field=problem_date_field,
        )
        if fallback_problems:
            st.warning("По выбранной дате данных нет, показаны последние доступные данные")
            report_date = fallback_report_date
            date_problems = fallback_problems
            break

unfiltered_problems = fetch_problems(
    report_date=report_date,
    seller_id=selected_seller,
    date_field=problem_date_field,
)
reason_options = unique_reasons(unfiltered_problems)
with st.sidebar:
    selected_reason = st.selectbox("Причина проблемы", reason_options)

problems = fetch_problems(
    report_date=report_date,
    seller_id=selected_seller,
    reason=selected_reason,
    date_field=problem_date_field,
)
problems_diagnostics = fetch_problems_diagnostics(
    report_date=report_date,
    date_field=problem_date_field,
    available_dates=report_dates,
)
quality = fetch_data_quality(report_date=report_date)

critical_sellers = len({row.get("seller_id") for row in problems if row.get("seller_id")})
critical_sku = len({row.get("nm_id") or row.get("nmId") for row in problems if row.get("nm_id") or row.get("nmId")})
reason = main_reason(problems)

card_1, card_2, card_3, card_4, card_5 = st.columns(5)
card_1.metric("Потеря выручки за день", format_money(sum(lost_revenue(row) for row in problems)))
card_2.metric("Потеря заказов за день", format_number(sum(lost_orders(row) for row in problems)))
card_3.metric("Критичные продавцы", format_number(critical_sellers))
card_4.metric("Критичные SKU", format_number(critical_sku))
card_5.metric("Главная причина просадок", reason)

st.subheader("Что смотреть первым")
if problems:
    top_problem = max(problems, key=lost_revenue)
    top_seller = sellers_by_id.get(str(top_problem.get("seller_id")), top_problem.get("seller_id") or "Без seller_id")
    st.info(
        f"Начните с продавца {top_seller}, SKU {top_problem.get('nm_id') or top_problem.get('nmId')}: "
        f"потеря {format_money(lost_revenue(top_problem))}, причина — {main_reason([top_problem])}."
    )
elif not date_problems:
    st.warning("По выбранной дате данные не найдены. Доступные даты в problems не содержат строк для выбранного фильтра даты.")
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


with st.sidebar:
    st.divider()
    st.subheader("Dashboard debug")
    st.caption(f"problems total rows found before date filter: {problems_diagnostics['total_rows_before_date_filter']}")
    st.caption(f"problems rows loaded after date filter: {problems_diagnostics['rows_loaded_after_date_filter']}")
    st.caption(f"date field used: {problems_diagnostics['date_field_used']}")
    st.caption(f"selected date: {problems_diagnostics['selected_date']}")
    st.caption(f"available dates count: {problems_diagnostics['available_dates_count']}")
