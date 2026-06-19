"""SKU card page for the Streamlit dashboard."""

from datetime import date, timedelta

import pandas as pd
import streamlit as st

from dashboard_queries import fetch_sku_ads_history, fetch_sku_history, fetch_sku_options, fetch_sku_problems
from formatters import (
    first_present,
    format_money,
    format_number,
    lost_orders,
    lost_revenue,
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


def _previous_period_bounds(start_date, end_date):
    days = (end_date - start_date).days + 1
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    return previous_start, previous_end


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



def _sum_metric(rows, aliases):
    return sum(to_number(first_present(row, aliases)) for row in rows)


def _average_metric(rows, aliases):
    values = [to_number(first_present(row, aliases)) for row in rows if first_present(row, aliases) not in (None, "")]
    return sum(values) / len(values) if values else None


def _period_metrics(history_rows, ads_rows):
    orders = _sum_metric(history_rows, ["orders", "order_count", "orderCount"])
    revenue = _sum_metric(history_rows, ["revenue", "order_sum", "orderSum"])
    opens = _sum_metric(history_rows, ["open_count", "openCount"])
    carts = _sum_metric(history_rows, ["cart_count", "cartCount"])
    avg_check = revenue / orders if orders else 0
    cart_conversion = (carts / opens * 100) if opens else _average_metric(history_rows, ["add_to_cart_percent", "addToCartPercent"])
    order_conversion = (orders / carts * 100) if carts else _average_metric(history_rows, ["cart_to_order_percent", "cartToOrderPercent"])
    impressions = _sum_metric(ads_rows, ["impressions", "views"])
    clicks = _sum_metric(ads_rows, ["clicks"])
    spend = _sum_metric(ads_rows, ["spend", "ad_spend", "advertising_cost"])
    ads_orders = _sum_metric(ads_rows, ["orders", "orders_count", "ordersCount"])
    ads_revenue = _sum_metric(ads_rows, ["revenue", "orders_sum", "ordersSum"])
    has_ads = bool(ads_rows) and any(first_present(row, ["impressions", "clicks", "spend", "ctr", "cpc", "drr"]) not in (None, "") for row in ads_rows)
    return {
        "orders": orders,
        "revenue": revenue,
        "avg_check": avg_check,
        "opens": opens,
        "carts": carts,
        "cart_conversion": cart_conversion,
        "order_conversion": order_conversion,
        "ctr": (clicks / impressions * 100) if impressions else _average_metric(ads_rows, ["ctr"]),
        "cpc": (spend / clicks) if clicks else _average_metric(ads_rows, ["cpc"]),
        "drr": (spend / ads_revenue * 100) if ads_revenue else _average_metric(ads_rows, ["drr"]),
        "ad_spend": spend,
        "ads_orders": ads_orders,
        "has_ads": has_ads,
    }


def _change_percent(current, previous):
    if previous in (None, 0) or current is None:
        return None
    return (current - previous) / previous * 100


def _metric_delta(current, previous):
    change = _change_percent(current, previous)
    return f"{change:+.1f}%" if change is not None else "—"


def _comparison_dataframe(current, previous):
    specs = [
        ("Заказы", "orders", format_number, "шт"),
        ("Выручка", "revenue", format_money, "₽"),
        ("Средний чек", "avg_check", format_money, "₽"),
        ("Переходы в карточку", "opens", format_number, "шт"),
        ("Добавления в корзину", "carts", format_number, "шт"),
        ("Конверсия в корзину", "cart_conversion", lambda value: f"{value:.1f}%" if value is not None else "—", "п.п."),
        ("Конверсия в заказ", "order_conversion", lambda value: f"{value:.1f}%" if value is not None else "—", "п.п."),
        ("CTR рекламы", "ctr", lambda value: f"{value:.1f}%" if value is not None else "—", "п.п."),
        ("CPC", "cpc", format_money, "₽"),
        ("ДРР", "drr", lambda value: f"{value:.1f}%" if value is not None else "—", "п.п."),
        ("Расход рекламы", "ad_spend", format_money, "₽"),
    ]
    records = []
    for name, key, formatter, _ in specs:
        cur = current.get(key)
        prev = previous.get(key)
        delta = _change_percent(cur, prev)
        if delta is None:
            conclusion = "нет базы для сравнения"
        elif abs(delta) < 5:
            conclusion = "стабильно"
        elif delta > 0:
            conclusion = "рост"
        else:
            conclusion = "снижение"
        records.append({"Метрика": name, "Текущий период": formatter(cur), "Прошлый период": formatter(prev), "Изменение": _metric_delta(cur, prev), "Вывод": conclusion})
    return pd.DataFrame(records)


def _has_confirmed_oos(rows):
    for row in rows:
        stock = first_present(row, ["real_sellable_stock", "realSellableStock"])
        state = str(first_present(row, ["stock_state", "stockState"], "")).upper()
        problem_type = str(first_present(row, ["problem_type", "problemType"], "")).lower()
        if stock not in (None, "") and to_number(stock) == 0:
            return True
        if state == "BLOCKED" or "outofstock" in problem_type or "oos" in problem_type:
            return True
    return False


def _build_sku_summary(current, previous, problem_rows):
    orders_delta = _change_percent(current["orders"], previous["orders"])
    revenue_delta = _change_percent(current["revenue"], previous["revenue"])
    status = "товар стабилен"
    if revenue_delta is not None and orders_delta is not None:
        if revenue_delta < -5 and orders_delta < -5:
            status = "товар просел"
        elif revenue_delta > 5 and orders_delta > 5:
            status = "товар вырос"
    ads_incomplete = not (current.get("has_ads") and previous.get("has_ads"))
    ads_worse = False if ads_incomplete else any([
        _change_percent(current.get("ctr"), previous.get("ctr")) is not None and _change_percent(current.get("ctr"), previous.get("ctr")) < -5,
        _change_percent(current.get("cpc"), previous.get("cpc")) is not None and _change_percent(current.get("cpc"), previous.get("cpc")) > 5,
        _change_percent(current.get("drr"), previous.get("drr")) is not None and _change_percent(current.get("drr"), previous.get("drr")) > 5,
    ])
    order_conv_drop = _change_percent(current.get("order_conversion"), previous.get("order_conversion"))
    if not problem_rows and not current["orders"] and not previous["orders"]:
        reason = "требует проверки"
    elif _has_confirmed_oos(problem_rows):
        reason = "остатки"
    elif status == "товар просел" and order_conv_drop is not None and order_conv_drop < -5:
        reason = "конверсия"
    elif ads_worse:
        reason = "реклама"
    else:
        reason = "требует проверки" if status == "товар просел" else "конверсия"
    lost_rev = max(previous["revenue"] - current["revenue"], 0)
    lost_ord = max(previous["orders"] - current["orders"], 0)
    confirmation = [f"Заказы: {format_number(current['orders'])} против {format_number(previous['orders'])} ({_metric_delta(current['orders'], previous['orders'])}); выручка: {format_money(current['revenue'])} против {format_money(previous['revenue'])} ({_metric_delta(current['revenue'], previous['revenue'])})."]
    if ads_incomplete:
        confirmation.append("Реклама требует проверки: данных недостаточно.")
    elif reason == "реклама":
        confirmation.append(f"CTR {_metric_delta(current.get('ctr'), previous.get('ctr'))}, CPC {_metric_delta(current.get('cpc'), previous.get('cpc'))}, ДРР {_metric_delta(current.get('drr'), previous.get('drr'))}.")
    elif reason == "конверсия":
        confirmation.append(f"Конверсия в заказ изменилась на {_metric_delta(current.get('order_conversion'), previous.get('order_conversion'))}.")
    elif reason == "остатки":
        confirmation.append("Есть подтверждение OOS или real_sellable_stock = 0.")
    actions = {
        "конверсия": ["Проверить цену, фото, отзывы и карточку против конкурентов.", "Найти дату просадки конверсии и сопоставить с изменениями карточки.", "Запустить точечные улучшения карточки и контролировать конверсию ежедневно."],
        "реклама": ["Проверить кампании с падением CTR или ростом CPC/ДРР.", "Снизить ставки или отключить неэффективные группы.", "Перераспределить бюджет на кампании с заказами и приемлемым ДРР."],
        "остатки": ["Проверить доступный остаток и статус товара на WB.", "Запланировать поставку или перераспределение со складов.", "Не усиливать рекламу до восстановления sellable stock."],
        "требует проверки": ["Проверить полноту данных по воронке, рекламе и остаткам.", "Сопоставить просадку с ценой, конкурентами и изменениями карточки."],
    }[reason]
    return status, reason, confirmation[:2], lost_rev, lost_ord, actions

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
    return pd.DataFrame(records).sort_values(["дата", "потеря выручки"], ascending=[False, False]) if records else pd.DataFrame()


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
    all_history_rows = fetch_sku_history(selected_nm_id, selected_seller)
    all_problem_rows = fetch_sku_problems(selected_nm_id, selected_seller)
    all_ads_rows = fetch_sku_ads_history(selected_nm_id, selected_seller)
    previous_start_date, previous_end_date = _previous_period_bounds(start_date, end_date)
    history_rows = _filter_rows_by_period(all_history_rows, start_date, end_date)
    previous_history_rows = _filter_rows_by_period(all_history_rows, previous_start_date, previous_end_date)
    problem_rows = _filter_rows_by_period(all_problem_rows, start_date, end_date)
    ads_rows = _filter_rows_by_period(all_ads_rows, start_date, end_date)
    previous_ads_rows = _filter_rows_by_period(all_ads_rows, previous_start_date, previous_end_date)
    current_metrics = _period_metrics(history_rows, ads_rows)
    previous_metrics = _period_metrics(previous_history_rows, previous_ads_rows)
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

    status, reason, confirmation, lost_revenue_total, lost_orders_total, actions = _build_sku_summary(current_metrics, previous_metrics, problem_rows)

    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Заказы", f"{format_number(current_metrics['orders'])} / {format_number(previous_metrics['orders'])}", _metric_delta(current_metrics["orders"], previous_metrics["orders"]))
    col2.metric("Выручка", f"{format_money(current_metrics['revenue'])} / {format_money(previous_metrics['revenue'])}", _metric_delta(current_metrics["revenue"], previous_metrics["revenue"]))
    col3.metric("Конверсия в корзину", f"{current_metrics['cart_conversion'] or 0:.1f}% / {previous_metrics['cart_conversion'] or 0:.1f}%", _metric_delta(current_metrics["cart_conversion"], previous_metrics["cart_conversion"]))
    col4.metric("Конверсия в заказ", f"{current_metrics['order_conversion'] or 0:.1f}% / {previous_metrics['order_conversion'] or 0:.1f}%", _metric_delta(current_metrics["order_conversion"], previous_metrics["order_conversion"]))
    ad1, ad2, ad3 = st.columns(3)
    ad1.metric("CTR рекламы", f"{current_metrics['ctr'] or 0:.1f}% / {previous_metrics['ctr'] or 0:.1f}%", _metric_delta(current_metrics["ctr"], previous_metrics["ctr"]))
    ad2.metric("CPC", f"{format_money(current_metrics['cpc'] or 0)} / {format_money(previous_metrics['cpc'] or 0)}", _metric_delta(current_metrics["cpc"], previous_metrics["cpc"]))
    ad3.metric("ДРР", f"{current_metrics['drr'] or 0:.1f}% / {previous_metrics['drr'] or 0:.1f}%", _metric_delta(current_metrics["drr"], previous_metrics["drr"]))

    st.subheader("Итог по SKU")
    st.markdown(f"**Итог:** {status}  \n**Главная причина:** {reason}  \n**Подтверждение:** {' '.join(confirmation)}  \n**Потеря выручки:** {format_money(lost_revenue_total)}  \n**Потеря заказов:** {format_number(round(lost_orders_total))}")
    st.markdown("**Что делать:**")
    for index, action in enumerate(actions, start=1):
        st.write(f"{index}. {action}")

    st.subheader("Сравнение периодов")
    st.caption(f"Текущий период: {start_date.isoformat()} — {end_date.isoformat()}; прошлый период: {previous_start_date.isoformat()} — {previous_end_date.isoformat()}")
    st.dataframe(_comparison_dataframe(current_metrics, previous_metrics), width="stretch", hide_index=True)

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
