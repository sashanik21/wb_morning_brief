"""SKU card page for the Streamlit dashboard."""

from datetime import date, datetime, timedelta
from html import escape

import pandas as pd
import streamlit as st

from supabase_client import get_supabase_client
from wb_dashboard_queries import (
    fetch_sku_ads_history,
    fetch_sku_change_log,
    fetch_sku_history,
    fetch_sku_options,
    fetch_sku_problems,
    fetch_sku_stocks_history,
)
from formatters import (
    first_present,
    format_money,
    format_number,
    lost_orders,
    lost_revenue,
    management_reason,
    reason_group,
    reason_explanation,
    reason_table_hint,
    sku_diagnosis,
    sku_main_reason,
    to_number,
)


def tooltip_text(text):
    return escape(str(text), quote=True).replace("\n", "&#10;")


def help_icon(help_text):
    return f'<span title="{tooltip_text(help_text)}">ⓘ</span>'


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
    "Потерянные заказы × средний чек. Если есть база сравнения, дополнительно смотрится разница выручки между текущим и прошлым периодом.",
    "Funnel API: воронка и продажи товара; сохранённые проблемные строки Dashboard.",
    "WB кабинет → Продажи → выбрать nm_id → сравнить выручку и заказы за текущий и прошлый период.",
    "Если нет прошлого периода или сохранённой проблемы, используется доступная разница периодов; при неполных данных значение может быть приблизительным.",
)
LOST_ORDERS_HELP = metric_tooltip(
    "Потеря заказов",
    "Разница заказов между прошлым и текущим периодом или сохранённая оценка потерь по проблеме SKU.",
    "Funnel API: воронка и продажи товара; сохранённые проблемные строки Dashboard.",
    "WB кабинет → Продажи → выбрать nm_id → сравнить количество заказов за два одинаковых периода.",
    "Если нет базы сравнения, система показывает только сохранённую оценку или 0; неполная история снижает точность.",
)
CONVERSION_HELP = metric_tooltip(
    "Конверсия",
    "Конверсия в корзину = корзины ÷ переходы × 100. Конверсия в заказ = заказы ÷ корзины × 100.",
    "Funnel API: переходы, корзины, заказы и продажи товара.",
    "WB/JEM → Воронка продаж по nm_id → сверить переходы, корзины, заказы и проценты конверсии.",
    "Если переходов или корзин нет, берётся сохранённый процент из истории; при неполной воронке показатель может отличаться от кабинета.",
)
CTR_HELP = metric_tooltip(
    "CTR рекламы",
    "Клики ÷ показы × 100. Если кликов или показов недостаточно, берётся сохранённый CTR кампании.",
    "Ads API: показы, клики и рекламные кампании.",
    "WB Продвижение → кампании → статистика по nm_id → сверить показы, клики и CTR.",
    "Если реклама не велась или данные кампаний неполные, показатель может быть 0 или усреднённым.",
)
CPC_HELP = metric_tooltip(
    "CPC",
    "Расход рекламы ÷ клики. Показывает среднюю цену клика за выбранный период.",
    "Ads API: расход и клики.",
    "WB Продвижение → кампании → статистика → сверить расход и клики по nm_id.",
    "Если кликов нет, используется сохранённый CPC; при неполной рекламе показатель может быть приблизительным.",
)
DRR_HELP = metric_tooltip(
    "ДРР",
    "Расход рекламы ÷ рекламная выручка × 100. Показывает долю рекламных расходов в выручке от рекламы.",
    "Ads API: расход, рекламные заказы и рекламная выручка.",
    "WB Продвижение → кампании → статистика → сверить расход, заказы и выручку по nm_id.",
    "Если рекламной выручки нет, берётся сохранённый ДРР; при неполной атрибуции рекламы показатель может отличаться.",
)
STOCK_HELP = metric_tooltip(
    "Остатки",
    "Берётся последний доступный остаток товара. Если остаток 0, система считает риск отсутствия товара подтверждённым.",
    "Stocks API: остатки по складам; сохранённые проблемы по товару.",
    "WB кабинет → Товары/Склады → остатки по nm_id → сверить доступный остаток и статус товара.",
    "Остатки могут обновляться с задержкой; если история не накоплена, система просит проверить товар вручную.",
)
SKU_DIAGNOSIS_HELP = metric_tooltip(
    "Диагноз SKU",
    "Это рейтинг влияния причин, а не вероятность и не точная модель. Вес причины строится по сигналам воронки, рекламы, остатков и сохранённых проблем.",
    "Funnel API, Ads API, Stocks API и Change Log товара.",
    "Проверить в WB/JEM: продажи и воронку по nm_id, рекламные кампании, остатки и последние изменения карточки.",
    "Если несколько причин близки по весу, диагноз неоднозначен. При неполных данных система показывает причину, которую нужно подтвердить вручную.",
)
AMBIGUOUS_REASON_HELP = metric_tooltip(
    "Причина не определена однозначно",
    "Несколько причин получили близкий вес, поэтому система не выбирает одну как явно главную.",
    "Funnel API, Ads API, Stocks API и сохранённые проблемы SKU.",
    "Сверить в WB/JEM динамику продаж, рекламы, остатков и изменений карточки за дату просадки.",
    "Требуется ручная проверка: данных недостаточно для уверенного вывода.",
)
MAIN_REASON_HELP = metric_tooltip(
    "Главная причина",
    "Выбирается причина с самым сильным подтверждением по потерям, воронке, рекламе, остаткам и изменениям товара.",
    "Funnel API, Ads API, Stocks API, Change Log и сохранённые проблемы Dashboard.",
    "В WB/JEM сверить метрику, которая просела сильнее всего: продажи, конверсию, рекламные показатели или остатки.",
    "Это управленческая подсказка, а не окончательный диагноз; при неполных данных причина требует проверки.",
)
REASON_LOSS_HELP = metric_tooltip(
    "Потери по причинам",
    "Сумма потерь по SKU, у которых эта причина определена как основная. Доля = потери причины ÷ все потери × 100.",
    "Сохранённые проблемы SKU, Funnel API, Ads API и Stocks API.",
    "В Dashboard открыть SKU из причины, затем в WB/JEM сверить продажи, воронку, рекламу и остатки по каждому nm_id.",
    "Если причина у SKU неоднозначна или данные неполные, сумма по причинам может быть приблизительной.",
)

def render_diagnosis_help(diagnosis_text):
    for line in diagnosis_text.splitlines():
        if line == "Причина не определена однозначно":
            st.markdown(f"{line} {help_icon(AMBIGUOUS_REASON_HELP)}", unsafe_allow_html=True)
        elif line.strip():
            st.markdown(f"{line} {help_icon(SKU_DIAGNOSIS_HELP)}", unsafe_allow_html=True)


PERIOD_OPTIONS = {
    "7 дней": 7,
    "14 дней": 14,
    "30 дней": 30,
    "90 дней": 90,
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
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    return text[:10] if text else None


def _period_bounds(period_label):
    today = date.today()
    days = PERIOD_OPTIONS[period_label]
    return today - timedelta(days=days - 1), today


def _previous_period_bounds(start_date, end_date):
    days = (end_date - start_date).days + 1
    return start_date - timedelta(days=days), end_date - timedelta(days=days)


def _format_period_range(start_date, end_date):
    if start_date == end_date:
        return start_date.isoformat()
    return f"{start_date.isoformat()} — {end_date.isoformat()}"


def _filter_rows_by_period(rows, start_date, end_date):
    start_text = _normalize_date(start_date)
    end_text = _normalize_date(end_date)
    filtered = []
    for row in rows:
        row_date = _normalize_date(_date_value(row))
        if row_date and start_text <= row_date <= end_text:
            filtered.append(row)
    return filtered


def _dedupe_rows(rows):
    deduped = {}
    for index, row in enumerate(rows):
        key = row.get("id") or (
            first_present(row, ["nm_id", "nmId", "nmID"]),
            _normalize_date(_date_value(row)),
            first_present(row, ["seller_id", "sellerId"]),
            index,
        )
        deduped[key] = row
    return list(deduped.values())


def _fetch_daily_funnel_rows_by_date(selected_nm_id, selected_seller, start_date, end_date):
    rows = fetch_sku_history(selected_nm_id, selected_seller, start_date, end_date)
    filtered_rows = _filter_rows_by_period(rows, start_date, end_date)
    if filtered_rows:
        return filtered_rows

    client = get_supabase_client()
    fallback_rows = []
    for nm_field in ("nm_id", "nmId", "nmID"):
        try:
            response = client.table("daily_funnel").select("*").eq(nm_field, selected_nm_id).limit(10000).execute()
            candidate_rows = response.data or []
        except Exception:
            continue
        if candidate_rows:
            fallback_rows = candidate_rows
            break

    if selected_seller and selected_seller != "Все продавцы":
        fallback_rows = [
            row
            for row in fallback_rows
            if str(first_present(row, ["seller_id", "sellerId"], "")) in ("", "None", str(selected_seller))
        ]
    return _dedupe_rows(_filter_rows_by_period([*rows, *fallback_rows], start_date, end_date))


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
                "Переходы": open_count,
                "Корзина": cart_count,
                "Заказы": orders,
                "Выручка": revenue,
                "Конверсия в корзину, %": to_number(add_to_cart),
                "Конверсия в заказ, %": conversion,
                "Остаток WB": to_number(first_present(row, ["wb_stocks", "wbStocks"])),
                "Доступный остаток": to_number(first_present(row, ["real_sellable_stock", "realSellableStock"])),
                "Средняя позиция": to_number(first_present(row, ["avg_position", "avgPosition"])),
                "Видимость": to_number(first_present(row, ["visibility_score", "visibilityScore"])),
            }
        )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values("Дата").set_index("Дата")



def _ads_dataframe(rows):
    records = []
    for row in rows:
        row_date = _normalize_date(_date_value(row))
        if not row_date:
            continue
        records.append(
            {
                "Дата": row_date,
                "Показы": to_number(first_present(row, ["impressions", "views"])),
                "Клики": to_number(first_present(row, ["clicks"])),
                "CTR": to_number(first_present(row, ["ctr"])),
                "CPC": to_number(first_present(row, ["cpc"])),
                "Расход": to_number(first_present(row, ["spend", "ad_spend", "advertising_cost"])),
                "Заказы рекламы": to_number(first_present(row, ["orders", "orders_count", "ordersCount"])),
                "Выручка рекламы": to_number(first_present(row, ["revenue", "orders_sum", "ordersSum"])),
                "ДРР": to_number(first_present(row, ["drr"])),
                "Кампания": first_present(row, ["campaign_id", "campaignId", "advert_id", "advertId", "advertising_campaign_id"], ""),
            }
        )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values(["Дата", "Расход"], ascending=[False, False]).set_index("Дата")


def _stocks_dataframe(rows):
    records = []
    for row in rows:
        row_date = _normalize_date(_date_value(row))
        if not row_date:
            continue
        records.append(
            {
                "Дата": row_date,
                "Склад": first_present(row, ["warehouse_name", "warehouseName"], ""),
                "Остаток": to_number(first_present(row, ["quantity", "qty", "stock", "stocks"])),
                "raw_json": first_present(row, ["raw_json", "rawJson"], ""),
            }
        )
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records).sort_values("Дата")


def _change_log_dataframe(rows):
    records = []
    for row in rows:
        records.append(
            {
                "Дата": _normalize_date(row.get("change_date")) or "",
                "Тип изменения": row.get("change_type") or "",
                "Было": row.get("old_value") or "",
                "Стало": row.get("new_value") or "",
                "Кто изменил": row.get("changed_by") or "",
                "Комментарий": row.get("comment") or "",
            }
        )
    return pd.DataFrame(records)


def _stock_chart_dataframe(stocks_df):
    if stocks_df.empty:
        return pd.DataFrame()
    return stocks_df.groupby("Дата", as_index=True)["Остаток"].sum().to_frame()


def _sum_metric(rows, aliases):
    values = [to_number(value) for row in rows if (value := first_present(row, aliases)) not in (None, "")]
    return sum(values) if values else None


def _average_metric(rows, aliases):
    values = [to_number(first_present(row, aliases)) for row in rows if first_present(row, aliases) not in (None, "")]
    return sum(values) / len(values) if values else None


def _period_metrics(history_rows, ads_rows):
    orders = _sum_metric(history_rows, ["orders", "order_count", "orderCount"])
    revenue = _sum_metric(history_rows, ["revenue", "order_sum", "orderSum"])
    opens = _sum_metric(history_rows, ["open_count", "openCount"])
    carts = _sum_metric(history_rows, ["cart_count", "cartCount"])
    avg_check = revenue / orders if revenue is not None and orders else None
    cart_conversion = (carts / opens * 100) if carts is not None and opens else _average_metric(history_rows, ["add_to_cart_percent", "addToCartPercent"])
    order_conversion = (orders / carts * 100) if orders is not None and carts else _average_metric(history_rows, ["cart_to_order_percent", "cartToOrderPercent"])
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
        "ctr": (clicks / impressions * 100) if clicks is not None and impressions else _average_metric(ads_rows, ["ctr"]),
        "cpc": (spend / clicks) if spend is not None and clicks else _average_metric(ads_rows, ["cpc"]),
        "drr": (spend / ads_revenue * 100) if spend is not None and ads_revenue else _average_metric(ads_rows, ["drr"]),
        "ad_spend": spend,
        "ads_orders": ads_orders,
        "has_history": bool(history_rows),
        "has_ads": has_ads,
    }


def _change_percent(current, previous):
    if previous in (None, 0) or current is None:
        return None
    return (current - previous) / previous * 100


def _metric_delta(current, previous):
    change = _change_percent(current, previous)
    return f"{change:+.1f}%" if change is not None else "—"


def _format_comparison_value(value, formatter, no_base=False):
    if value is None:
        return "нет базы для сравнения" if no_base else "нет данных"
    return formatter(value)


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
        no_base = not previous.get("has_history") and not previous.get("has_ads")
        records.append({
            "Метрика": name,
            "Текущий период": _format_comparison_value(cur, formatter),
            "Прошлый период": _format_comparison_value(prev, formatter, no_base=no_base),
            "Изменение": _metric_delta(cur, prev),
            "Вывод": conclusion,
        })
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


def _stock_snapshot(rows):
    latest = _latest_row(rows)
    if not latest:
        return None, "нет данных", "Остатки требуют проверки: история остатков пока не накоплена"
    quantity = to_number(
        first_present(
            latest,
            [
                "real_sellable_stock",
                "realSellableStock",
                "quantity",
                "qty",
                "stock",
                "stocks",
                "wb_stocks",
                "wbStocks",
            ],
        )
    )
    if quantity <= 0:
        return quantity, "подтверждённый OOS", "realSellableStock / quantity = 0"
    return quantity, "есть остаток", f"доступный остаток: {format_number(quantity)}"


def _has_confirmed_stock_cause(stock_rows, problem_rows):
    quantity, stock_status, _ = _stock_snapshot(stock_rows)
    if stock_status == "подтверждённый OOS":
        return True
    if stock_status == "нет данных":
        return False
    return _has_confirmed_oos(problem_rows) and quantity == 0


def _format_transition(name, current, previous):
    previous_text = "нет данных" if previous is None else format_number(previous)
    current_text = "нет данных" if current is None else format_number(current)
    return f"{name}: {previous_text} → {current_text} ({_metric_delta(current, previous)})"


def _problem_priority(row):
    return (
        lost_revenue(row),
        lost_orders(row),
        to_number(first_present(row, ["businessImpactScore", "business_impact_score"])),
        to_number(first_present(row, ["severityScore", "severity_score"])),
        to_number(first_present(row, ["dynamicPercent", "dynamic_percent"])),
    )


def _top_sku_problem_row(problem_rows):
    if not problem_rows:
        return {}
    return max(problem_rows, key=_problem_priority)


def _top_sku_loss(problem_rows):
    top_problem = _top_sku_problem_row(problem_rows)
    return lost_revenue(top_problem), lost_orders(top_problem)


def _has_comparison_base(previous):
    return any(previous.get(key) is not None for key in ("orders", "revenue", "opens", "carts"))



def _build_sku_summary(current, previous, problem_rows, stock_rows):
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
    if not problem_rows and not current.get("orders") and not previous.get("orders"):
        reason = "требует проверки"
    elif _has_confirmed_stock_cause(stock_rows, problem_rows):
        reason = "остатки"
    elif status == "товар просел" and order_conv_drop is not None and order_conv_drop < -5:
        reason = "конверсия"
    elif ads_worse:
        reason = "реклама"
    else:
        reason = "требует проверки" if status == "товар просел" else "конверсия"
    top_lost_rev, top_lost_ord = _top_sku_loss(problem_rows)
    calculated_lost_rev = 0
    calculated_lost_ord = 0
    if previous.get("revenue") is not None and current.get("revenue") is not None:
        calculated_lost_rev = max(previous["revenue"] - current["revenue"], 0)
    if previous.get("orders") is not None and current.get("orders") is not None:
        calculated_lost_ord = max(previous["orders"] - current["orders"], 0)
    lost_rev = top_lost_rev if top_lost_rev else calculated_lost_rev
    lost_ord = top_lost_ord if top_lost_ord else calculated_lost_ord
    if problem_rows and status == "товар стабилен":
        status = "sku теряет" if (lost_rev > 0 or lost_ord > 0) else "требует внимания"
    if problem_rows:
        reason = sku_main_reason(problem_rows)
    _, stock_status, stock_confirmation = _stock_snapshot(stock_rows)
    if _has_comparison_base(previous):
        confirmation = [
            _format_transition("Переходы", current.get("opens"), previous.get("opens")),
            _format_transition("Корзина", current.get("carts"), previous.get("carts")),
            _format_transition("Заказы", current.get("orders"), previous.get("orders")),
        ]
    else:
        confirmation = ["Нет базы для сравнения за прошлый период."]
    if ads_incomplete:
        confirmation.append("Реклама требует проверки: данных недостаточно.")
    elif reason == "реклама":
        confirmation.append(f"CTR {_metric_delta(current.get('ctr'), previous.get('ctr'))}, CPC {_metric_delta(current.get('cpc'), previous.get('cpc'))}, ДРР {_metric_delta(current.get('drr'), previous.get('drr'))}.")
    elif reason == "конверсия":
        confirmation.append(f"Конверсия в заказ изменилась на {_metric_delta(current.get('order_conversion'), previous.get('order_conversion'))}.")
    elif reason == "остатки":
        confirmation.append(stock_confirmation)
    actions = {
        "конверсия": ["Проверить цену, фото, отзывы и карточку против конкурентов.", "Найти дату просадки конверсии и сопоставить с изменениями карточки.", "Запустить точечные улучшения карточки и контролировать конверсию ежедневно."],
        "реклама": ["Проверить кампании с падением CTR или ростом CPC/ДРР.", "Снизить ставки или отключить неэффективные группы.", "Перераспределить бюджет на кампании с заказами и приемлемым ДРР."],
        "остатки": ["Проверить доступный остаток и статус товара на WB.", "Запланировать поставку или перераспределение со складов.", "Не усиливать рекламу до восстановления sellable stock."],
        "требует проверки": ["Проверить полноту данных по воронке, рекламе и остаткам.", "Сопоставить просадку с ценой, конкурентами и изменениями карточки."],
    }[reason]
    if stock_status == "нет данных" and reason != "остатки":
        confirmation.append(stock_confirmation)
    return status, reason, confirmation[:3], lost_rev, lost_ord, actions

def _problem_table(rows):
    records = []
    for row in rows:
        reason = management_reason(row)
        records.append(
            {
                "дата": _normalize_date(_date_value(row)) or "",
                "причина": reason,
                "метрика": first_present(row, ["metric", "decline_source", "problem_type", "problemLabel", "problem_label"], ""),
                "изменение": first_present(row, ["change_percent", "changePercent", "delta", "decline_percent", "declinePercent"], ""),
                "потеря выручки": lost_revenue(row),
                "потеря заказов": round(lost_orders(row)),
                "рекомендация": first_present(row, ["root_recommendation", "recommendation", "forecast_message"], "")
                or reason_table_hint(reason),
            }
        )
    return pd.DataFrame(records).sort_values(["дата", "потеря выручки"], ascending=[False, False]) if records else pd.DataFrame()


def _latest_row(rows):
    dated_rows = [(_normalize_date(_date_value(row)) or "", row) for row in rows]
    if not dated_rows:
        return {}
    return sorted(dated_rows, key=lambda item: item[0], reverse=True)[0][1]


def _campaign_count(rows):
    campaign_ids = {
        first_present(row, ["campaign_id", "campaignId", "advert_id", "advertId", "advertising_campaign_id"])
        for row in rows
        if first_present(row, ["campaign_id", "campaignId", "advert_id", "advertId", "advertising_campaign_id"]) not in (None, "")
    }
    if campaign_ids:
        return len(campaign_ids)
    return len(rows) if rows else 0


def _stock_total(rows):
    latest = _latest_row(rows)
    if not latest:
        return 0
    return to_number(
        first_present(
            latest,
            [
                "real_sellable_stock",
                "realSellableStock",
                "quantity",
                "qty",
                "stock",
                "stocks",
                "wb_stocks",
                "wbStocks",
            ],
        )
    )


def _stock_status(rows):
    latest = _latest_row(rows)
    if not latest:
        return "нет данных"
    explicit_status = first_present(latest, ["stock_state", "stockState", "status"])
    if explicit_status not in (None, ""):
        return str(explicit_status)
    stock = _stock_total(rows)
    if stock <= 0:
        return "нет остатка"
    if stock < 5:
        return "низкий остаток"
    return "в наличии"



def _ads_diagnosis(current, previous):
    if not current.get("has_ads"):
        return "🟡 Данных недостаточно", ["для рекламного диагноза нет данных за текущий период."]
    if not previous.get("has_ads"):
        return "🟡 Данных недостаточно", ["для сравнения рекламной истории недостаточно."]

    ctr_change = _change_percent(current.get("ctr"), previous.get("ctr"))
    cpc_change = _change_percent(current.get("cpc"), previous.get("cpc"))
    drr_change = _change_percent(current.get("drr"), previous.get("drr"))
    evidence = [
        f"CTR: {_format_comparison_value(previous.get('ctr'), lambda value: f'{value:.1f}%')} → {_format_comparison_value(current.get('ctr'), lambda value: f'{value:.1f}%')}",
        f"CPC: {_format_comparison_value(previous.get('cpc'), format_money)} → {_format_comparison_value(current.get('cpc'), format_money)}",
        f"ДРР: {_format_comparison_value(previous.get('drr'), lambda value: f'{value:.1f}%')} → {_format_comparison_value(current.get('drr'), lambda value: f'{value:.1f}%')}",
    ]
    if any(change is not None and change > 5 for change in (cpc_change, drr_change)) or (ctr_change is not None and ctr_change < -5):
        return "🔴 Реклама ухудшилась", evidence
    if ctr_change is not None and ctr_change > 5 and drr_change is not None and drr_change < -5:
        return "🟢 Реклама помогает", evidence
    return "⚪ Реклама не является основной причиной", evidence


def _problem_confirmation(row, reason):
    if reason == "остатки":
        value = first_present(row, ["real_sellable_stock", "realSellableStock", "stock_state", "stockState", "problem_type", "problemType"])
        return f"realSellableStock / остаток: {value}" if value not in (None, "") else "есть stock-сигнал в problems"
    metric = first_present(row, ["metric", "decline_source", "problem_type", "problemLabel", "problem_label"], "метрика просела")
    change = first_present(row, ["change_percent", "changePercent", "delta", "decline_percent", "declinePercent"], "")
    return f"{metric}: {change}" if change not in (None, "") else str(metric)


def _problem_summary_table(rows, stock_rows):
    grouped = {}
    stock_status = _stock_snapshot(stock_rows)[1]
    for row in rows:
        key = reason_group(row)
        reason = management_reason(row)
        if key == "stocks" and stock_status != "подтверждённый OOS":
            reason = "требует проверки"
        summary = grouped.setdefault(
            reason,
            {
                "Причина": reason,
                "Потеря выручки": 0,
                "Потеря заказов": 0,
                "Количество сигналов": 0,
                "Главное подтверждение": "",
                "Действие": reason_table_hint(reason),
            },
        )
        summary["Потеря выручки"] += lost_revenue(row)
        summary["Потеря заказов"] += lost_orders(row)
        summary["Количество сигналов"] += 1
        if not summary["Главное подтверждение"] or lost_revenue(row) > summary.get("_top_loss", -1):
            summary["Главное подтверждение"] = _problem_confirmation(row, reason)
            summary["_top_loss"] = lost_revenue(row)
            summary["Действие"] = first_present(row, ["root_recommendation", "recommendation", "forecast_message"], "") or reason_table_hint(reason)
    records = []
    for summary in grouped.values():
        summary.pop("_top_loss", None)
        summary["Потеря заказов"] = round(summary["Потеря заказов"])
        records.append(summary)
    return pd.DataFrame(records).sort_values(["Потеря выручки", "Потеря заказов"], ascending=[False, False]) if records else pd.DataFrame()

def _problem_description(problem_rows, reason):
    latest_problem = _latest_row(problem_rows)
    if not latest_problem:
        return reason_explanation(reason)
    return first_present(
        latest_problem,
        ["root_cause_description", "reason_description", "diagnosis", "forecast_message", "recommendation", "root_recommendation"],
        reason_explanation(reason),
    )


def _set_dashboard_query_params():
    try:
        st.query_params.clear()
        st.query_params["mode"] = "Executive Dashboard"
    except AttributeError:
        st.experimental_set_query_params(mode="Executive Dashboard")


def render_sku_page(sellers, sellers_by_id, initial_nm_id=None, selected_seller=None):
    """Render SKU detail page with diagnostics from existing dashboard tables."""
    st.title("Карточка SKU")
    st.caption("Диагностика конкретного артикула Wildberries по существующим данным Dashboard")

    if st.button("← Назад к Dashboard"):
        _set_dashboard_query_params()
        st.rerun()

    selected_seller = "Все продавцы"

    sku_options = fetch_sku_options(selected_seller)
    if not sku_options:
        st.warning("Артикулы WB не найдены в products или problems.")
        return

    sku_by_id = {str(option["nm_id"]): option for option in sku_options}
    selected_nm_id = str(initial_nm_id) if initial_nm_id and str(initial_nm_id) in sku_by_id else list(sku_by_id)[0]
    selected_nm_id = st.selectbox(
        "Артикул WB",
        list(sku_by_id),
        index=list(sku_by_id).index(selected_nm_id),
        format_func=lambda value: f"{value} — {sku_by_id[value].get('title') or 'без названия'}",
    )

    period_label = st.selectbox("Период истории", list(PERIOD_OPTIONS), index=2)
    start_date, end_date = _period_bounds(period_label)
    previous_start, previous_end = _previous_period_bounds(start_date, end_date)

    product = sku_by_id[selected_nm_id]
    history_rows = _fetch_daily_funnel_rows_by_date(selected_nm_id, selected_seller, start_date, end_date)
    previous_history_rows = _fetch_daily_funnel_rows_by_date(selected_nm_id, selected_seller, previous_start, previous_end)
    problem_rows = fetch_sku_problems(selected_nm_id, selected_seller, start_date, end_date)
    ads_rows = fetch_sku_ads_history(selected_nm_id, selected_seller, start_date, end_date)
    previous_ads_rows = fetch_sku_ads_history(selected_nm_id, selected_seller, previous_start, previous_end)
    stock_rows = fetch_sku_stocks_history(selected_nm_id, selected_seller, start_date, end_date)
    change_log_rows = fetch_sku_change_log(selected_nm_id, selected_seller, start_date, end_date)
    current_metrics = _period_metrics(history_rows, ads_rows)
    previous_metrics = _period_metrics(previous_history_rows, previous_ads_rows)
    latest_problem = _latest_row(problem_rows)
    status, summary_reason, confirmation, lost_rev, lost_ord, actions = _build_sku_summary(current_metrics, previous_metrics, problem_rows, stock_rows)
    reason = summary_reason
    history_df = _history_dataframe(history_rows)
    ads_df = _ads_dataframe(ads_rows)
    stocks_df = _stocks_dataframe(stock_rows)
    stock_chart_df = _stock_chart_dataframe(stocks_df)
    change_log_df = _change_log_dataframe(change_log_rows)
    seller_article = product.get("vendor_code") or product.get("vendorCode") or product.get("supplier_article") or product.get("supplierArticle") or "—"
    abc = first_present(latest_problem, ["abc", "abc_class", "abcClass", "abc_segment", "abcSegment"]) or first_present(product, ["abc", "abc_class", "abcClass", "abc_segment", "abcSegment"], "—")

    st.subheader(product.get("title") or "Без названия")
    st.caption(f"История за период: {_format_period_range(start_date, end_date)}")
    st.caption(f"Прошлый период: {_format_period_range(previous_start, previous_end)}")
    st.markdown(
        """
        <style>
        .sku-info-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.5rem;
            margin: 0.35rem 0 0.8rem;
        }
        .sku-info-card {
            border: 1px solid rgba(49, 51, 63, 0.18);
            border-radius: 0.45rem;
            padding: 0.45rem 0.6rem;
            min-height: 3.1rem;
            background: rgba(250, 250, 250, 0.6);
        }
        .sku-info-label {
            color: rgba(49, 51, 63, 0.65);
            font-size: 0.68rem;
            line-height: 1.05;
            margin-bottom: 0.18rem;
        }
        .sku-info-value {
            color: rgb(49, 51, 63);
            font-size: 0.92rem;
            font-weight: 600;
            line-height: 1.2;
            overflow-wrap: anywhere;
            word-break: break-word;
            white-space: normal;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    info_cards = [
        ("WB артикул", selected_nm_id),
        ("Артикул продавца", seller_article),
        ("Название", product.get("title") or "—"),
        ("ABC", abc),
    ]
    st.markdown(
        '<div class="sku-info-grid">'
        + "".join(
            '<div class="sku-info-card">'
            f'<div class="sku-info-label">{escape(str(label))}</div>'
            f'<div class="sku-info-value">{escape(str(value))}</div>'
            "</div>"
            for label, value in info_cards
        )
        + "</div>",
        unsafe_allow_html=True,
    )

    overview_tab, sales_tab, ads_tab, stocks_tab, problems_tab, changes_tab = st.tabs(["Обзор", "Продажи и воронка", "Реклама", "Остатки", "Проблемы", "История изменений"])

    with overview_tab:
        st.subheader("🔴 Диагноз SKU")
        diagnosis_text = sku_diagnosis(problem_rows) if problem_rows else ""
        if lost_rev > 0 or lost_ord > 0:
            st.markdown(f"**🔴 SKU теряет {format_money(lost_rev)}**")
        else:
            st.markdown(f"**{status.capitalize()}.**")
        if diagnosis_text:
            st.markdown(f"**Диагноз SKU:** {help_icon(SKU_DIAGNOSIS_HELP)}", unsafe_allow_html=True)
            render_diagnosis_help(diagnosis_text)
        diag_1, diag_2, diag_3 = st.columns(3)
        diag_1.metric("Потеря выручки ⓘ", format_money(lost_rev), help=LOST_REVENUE_HELP)
        diag_2.metric("Потеря заказов ⓘ", format_number(round(lost_ord)), help=LOST_ORDERS_HELP)
        diag_3.metric("Главная причина ⓘ", summary_reason, help=MAIN_REASON_HELP)
        st.markdown("**Подтверждение:**")
        for item in confirmation:
            st.write(f"- {item}")
        st.markdown("**Что делать:**")
        for item in actions[:3]:
            st.write(f"- {item}")

        st.subheader("Сравнение периодов")
        st.dataframe(
            _comparison_dataframe(current_metrics, previous_metrics),
            width="stretch",
            hide_index=True,
            column_config={
                "Метрика": st.column_config.TextColumn("Метрика ⓘ", help=CONVERSION_HELP),
                "Текущий период": st.column_config.TextColumn("Текущий период ⓘ", help=CONVERSION_HELP),
                "Изменение": st.column_config.TextColumn("Изменение ⓘ", help="Изменение считается как разница между текущим и прошлым периодом в процентах."),
            },
        )

        st.subheader("Последние изменения")
        if change_log_df.empty:
            st.info("Изменения по SKU не найдены.")
        else:
            st.dataframe(change_log_df.head(3), width="stretch", hide_index=True)
        st.subheader("Конкуренты")
        st.info("пока не подключены")

    with sales_tab:
        st.subheader("Продажи")
        sales_1, sales_2 = st.columns(2)
        sales_1.metric("Потеря выручки ⓘ", format_money(lost_rev), help=LOST_REVENUE_HELP)
        sales_2.metric("Потеря заказов ⓘ", format_number(round(lost_ord)), help=LOST_ORDERS_HELP)
        if history_df.empty:
            st.info("История продаж и воронки за выбранный период не найдена.")
        else:
            st.subheader("Динамика по дням")
            daily_df = history_df.reset_index().rename(
                columns={
                    "Конверсия в заказ, %": "Конверсия",
                    "Остаток WB": "Остаток",
                    "Средняя позиция": "Позиция",
                }
            )
            st.dataframe(
                daily_df[["Дата", "Переходы", "Корзина", "Заказы", "Выручка", "Конверсия", "Остаток", "Позиция"]],
                width="stretch",
                hide_index=True,
            )
            st.subheader("Выручка по дням")
            st.line_chart(history_df[["Выручка"]])
            st.subheader("Заказы по дням")
            st.line_chart(history_df[["Заказы"]])
            st.subheader("Воронка")
            funnel_1, funnel_2, funnel_3, funnel_4 = st.columns(4)
            funnel_1.metric("Переходы", format_number(current_metrics["opens"]))
            funnel_2.metric("Корзина", format_number(current_metrics["carts"]))
            funnel_3.metric("Конверсия в корзину ⓘ", f"{current_metrics['cart_conversion'] or 0:.1f}%", help=CONVERSION_HELP)
            funnel_4.metric("Конверсия в заказ ⓘ", f"{current_metrics['order_conversion'] or 0:.1f}%", help=CONVERSION_HELP)
            st.subheader("Переходы → Корзина → Заказы")
            st.line_chart(history_df[["Переходы", "Корзина", "Заказы"]])
            st.subheader("Конверсия в корзину и конверсия в заказ")
            st.line_chart(history_df[["Конверсия в корзину, %", "Конверсия в заказ, %"]])

    with ads_tab:
        st.subheader("Реклама")
        ads_1, ads_2, ads_3, ads_4 = st.columns(4)
        ads_1.metric("CTR рекламы ⓘ", f"{current_metrics['ctr'] or 0:.1f}%", help=CTR_HELP)
        ads_2.metric("CPC ⓘ", format_money(current_metrics["cpc"] or 0), help=CPC_HELP)
        ads_3.metric("ДРР ⓘ", f"{current_metrics['drr'] or 0:.1f}%", help=DRR_HELP)
        ads_4.metric("Количество кампаний", format_number(_campaign_count(ads_rows)))
        ads_diagnosis, ads_evidence = _ads_diagnosis(current_metrics, previous_metrics)
        st.markdown(f"**Рекламный диагноз:** {ads_diagnosis} {help_icon(SKU_DIAGNOSIS_HELP)}", unsafe_allow_html=True)
        for item in ads_evidence:
            st.caption(item)
        if ads_df.empty:
            st.info("История рекламы за выбранный период не найдена.")
        else:
            st.line_chart(ads_df[["Показы", "Клики", "CTR", "CPC", "ДРР", "Расход"]])
            st.dataframe(ads_df.reset_index()[["Дата", "Кампания", "Показы", "Клики", "CTR", "CPC", "Расход", "Заказы рекламы", "Выручка рекламы", "ДРР"]], width="stretch", hide_index=True)

    with stocks_tab:
        st.subheader("Остатки")
        stock_1, stock_2 = st.columns(2)
        stock_quantity, stock_status, _ = _stock_snapshot(stock_rows)
        stock_1.metric("Остаток ⓘ", "—" if stock_quantity is None else format_number(stock_quantity), help=STOCK_HELP)
        stock_2.metric("Статус остатков ⓘ", stock_status, help=STOCK_HELP)
        if stock_chart_df.empty:
            st.info("История остатков пока не накоплена.")
        else:
            st.line_chart(stock_chart_df)
            st.dataframe(stocks_df, width="stretch", hide_index=True)

    with problems_tab:
        st.subheader("Сводка проблем по SKU")
        summary_df = _problem_summary_table(problem_rows, stock_rows)
        if summary_df.empty:
            st.success("Проблемы по SKU не найдены.")
        else:
            st.dataframe(
                summary_df.reset_index(drop=True),
                width="stretch",
                hide_index=True,
                column_config={
                    "Потеря выручки": st.column_config.NumberColumn("Потеря выручки ⓘ", help=LOST_REVENUE_HELP),
                    "Потеря заказов": st.column_config.NumberColumn("Потеря заказов ⓘ", help=LOST_ORDERS_HELP),
                    "Причина": st.column_config.TextColumn("Причина ⓘ", help=REASON_LOSS_HELP),
                    "Главное подтверждение": st.column_config.TextColumn("Главное подтверждение ⓘ", help=MAIN_REASON_HELP),
                },
            )
        st.markdown(
            f"**Главная причина:** {reason} {help_icon(MAIN_REASON_HELP)}  \n"
            f"**Описание причины:** {_problem_description(problem_rows, reason)}",
            unsafe_allow_html=True,
        )
        with st.expander("Показать технические строки problems", expanded=False):
            problems_df = _problem_table(problem_rows)
            if problems_df.empty:
                st.info("Технических строк problems нет.")
            else:
                st.dataframe(problems_df.reset_index(drop=True), width="stretch", hide_index=True)

    with changes_tab:
        st.subheader("История изменений")
        if change_log_df.empty:
            st.info("История изменений пока не заполнена.")
        else:
            st.dataframe(change_log_df, width="stretch", hide_index=True)
