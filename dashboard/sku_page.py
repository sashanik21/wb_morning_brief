"""SKU card page for the Streamlit dashboard."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard_queries import fetch_sku_history, fetch_sku_options, fetch_sku_problems
from formatters import (
    first_present,
    format_money,
    format_number,
    lost_orders,
    lost_revenue,
    main_reason,
    management_reason,
    reason_table_hint,
    to_number,
)


PERIOD_OPTIONS = {
    "7 дней": 7,
    "14 дней": 14,
    "30 дней": 30,
    "Произвольный период": None,
}
CHECKLIST_BY_REASON = {
    "конверсия": "проверить фото, цену, отзывы, карточку и конкурентов",
    "реклама": "проверить CTR, CPC, ДРР, ставки и кампании",
    "реклама остановлена": "проверить CTR, CPC, ДРР, ставки и кампании",
    "остатки": "проверить доступность товара и поставки",
    "цена": "сравнить цену с конкурентами",
    "требует проверки": "проверить карточку, рекламу, цену и конкурентов вручную",
}


def _date_value(row):
    return first_present(row, ["date", "report_date", "created_at"])


def _normalize_date(value):
    if value in (None, ""):
        return None
    return str(value)[:10]


def _period_bounds(period_label):
    today = date.today()
    days = PERIOD_OPTIONS[period_label]
    if days:
        return today - timedelta(days=days - 1), today

    selected = st.date_input(
        "Произвольный период",
        value=(today - timedelta(days=29), today),
        help="Выберите начальную и конечную дату периода.",
    )
    if isinstance(selected, tuple) and len(selected) == 2:
        return selected
    return today - timedelta(days=29), today


def _filter_rows_by_period(rows, start_date, end_date):
    start_text = start_date.isoformat()
    end_text = end_date.isoformat()
    filtered = []
    for row in rows:
        row_date = _normalize_date(_date_value(row))
        if row_date and start_text <= row_date <= end_text:
            filtered.append(row)
    return filtered


def _history_dataframe(rows):
    records = []
    for row in rows:
        row_date = _normalize_date(_date_value(row))
        if not row_date:
            continue
        orders = to_number(first_present(row, ["orders", "order_count", "orderCount"]))
        revenue = to_number(first_present(row, ["revenue", "order_sum", "orderSum"]))
        cart_to_order = first_present(row, ["cart_to_order_percent", "cartToOrderPercent"])
        add_to_cart = first_present(row, ["add_to_cart_percent", "addToCartPercent"])
        open_count = to_number(first_present(row, ["open_count", "openCount"]))
        cart_count = to_number(first_present(row, ["cart_count", "cartCount"]))
        conversion = to_number(cart_to_order if cart_to_order not in (None, "") else add_to_cart)
        if conversion == 0 and open_count:
            conversion = orders / open_count * 100
        elif conversion == 0 and cart_count:
            conversion = orders / cart_count * 100
        records.append(
            {
                "Дата": row_date,
                "Заказы": orders,
                "Выручка": revenue,
                "Конверсия в заказ, %": conversion,
            }
        )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values("Дата").set_index("Дата")


def _problem_table(rows):
    records = []
    for row in rows:
        reason = management_reason(row)
        records.append(
            {
                "дата": _normalize_date(_date_value(row)) or "",
                "причина": reason,
                "потеря выручки": lost_revenue(row),
                "потеря заказов": round(lost_orders(row)),
                "подтверждение": first_present(row, ["impact_confidence", "report_trust_score", "reportTrustScore"], ""),
                "действие": first_present(row, ["root_recommendation", "recommendation", "forecast_message"], "")
                or reason_table_hint(reason),
            }
        )
    return pd.DataFrame(records).sort_values("дата", ascending=False) if records else pd.DataFrame()


def render_sku_page(sellers, sellers_by_id):
    """Render SKU history mode."""
    st.title("Карточка SKU")
    st.caption("История конкретной карточки Wildberries за выбранный период")

    seller_options = [str(row.get("seller_id") or row.get("id")) for row in sellers if row.get("seller_id") or row.get("id")]
    if not seller_options:
        seller_options = ["Все продавцы"]
    selected_seller = st.selectbox(
        "Продавец",
        seller_options,
        format_func=lambda value: sellers_by_id.get(str(value), str(value)),
    )

    sku_options = fetch_sku_options(selected_seller)
    if not sku_options:
        st.warning("Артикулы WB не найдены в products или problems.")
        return

    sku_by_id = {str(option["nm_id"]): option for option in sku_options}
    selected_nm_id = st.selectbox(
        "Артикул WB",
        list(sku_by_id),
        format_func=lambda value: f"{value} — {sku_by_id[value].get('title') or 'без названия'}",
    )
    period_label = st.selectbox("Период", list(PERIOD_OPTIONS), index=0)
    start_date, end_date = _period_bounds(period_label)

    product = sku_by_id[selected_nm_id]
    history_rows = _filter_rows_by_period(fetch_sku_history(selected_nm_id, selected_seller), start_date, end_date)
    problem_rows = _filter_rows_by_period(fetch_sku_problems(selected_nm_id, selected_seller), start_date, end_date)
    history_df = _history_dataframe(history_rows)

    st.subheader(product.get("title") or "Без названия")
    seller_name = sellers_by_id.get(str(selected_seller), selected_seller)
    seller_article = product.get("vendor_code") or product.get("vendorCode") or product.get("supplier_article") or product.get("supplierArticle") or "—"
    st.markdown(
        f"**Продавец:** {seller_name}  \n"
        f"**Артикул WB:** {selected_nm_id}  \n"
        f"**Артикул продавца:** {seller_article}  \n"
        f"**Ссылка WB:** https://www.wildberries.ru/catalog/{selected_nm_id}/detail.aspx"
    )

    orders_total = history_df["Заказы"].sum() if not history_df.empty and "Заказы" in history_df else 0
    revenue_total = history_df["Выручка"].sum() if not history_df.empty and "Выручка" in history_df else 0
    avg_check = revenue_total / orders_total if orders_total else 0
    lost_revenue_total = sum(lost_revenue(row) for row in problem_rows)
    lost_orders_total = sum(lost_orders(row) for row in problem_rows)
    reason = main_reason(problem_rows)

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Заказы", format_number(orders_total))
    col2.metric("Выручка", format_money(revenue_total))
    col3.metric("Средний чек", format_money(avg_check))
    col4.metric("Потеря выручки", format_money(lost_revenue_total))
    col5.metric("Потеря заказов", format_number(round(lost_orders_total)))
    col6.metric("Главная причина", reason)

    st.subheader("Динамика")
    if history_df.empty:
        st.info("История daily_funnel за выбранный период не найдена.")
    else:
        st.line_chart(history_df[["Заказы"]])
        st.line_chart(history_df[["Выручка"]])
        st.line_chart(history_df[["Конверсия в заказ, %"]])

    st.subheader("История проблем")
    problems_df = _problem_table(problem_rows)
    if problems_df.empty:
        st.success("Проблемы по SKU за выбранный период не найдены.")
    else:
        st.dataframe(problems_df.reset_index(drop=True), width="stretch", hide_index=True)

    st.subheader("Что проверить")
    st.info(CHECKLIST_BY_REASON.get(reason, CHECKLIST_BY_REASON["требует проверки"]))
