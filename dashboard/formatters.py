"""Formatting and normalization helpers for dashboard data."""

import pandas as pd


MONEY_COLUMNS = ["lost_revenue"]
INTEGER_COLUMNS = ["lost_orders", "critical_sku"]
REASON_FILTER_OPTIONS = [
    "Все причины",
    "Конверсия",
    "Реклама",
    "Реклама остановлена",
    "Заказы",
    "Выручка",
    "Остатки",
    "Цена",
    "Требует проверки",
]
REASON_LABELS = {
    "conversion": "конверсия",
    "ads": "реклама",
    "ads_stopped": "реклама остановлена",
    "orders": "заказы",
    "revenue": "выручка",
    "stocks": "остатки",
    "price": "цена",
    "unknown": "требует проверки",
}
STATUS_LABELS = {
    "critical": "критично",
    "warning": "требует внимания",
    "ok": "стабильно",
    "watch": "требует внимания",
}

REASON_EXPLANATIONS = {
    "конверсия": "Проблема внутри карточки или предложения. Проверяются переходы, корзина, заказ, конверсия в корзину и конверсия в заказ.",
    "реклама": "Проблема в рекламном трафике или его качестве. Проверяются CTR, CPC, ДРР, клики, рекламные заказы и покрытие рекламы.",
    "реклама остановлена": "Рекламная кампания остановлена или не даёт трафик. Нужно проверить статус кампании, бюджет и ставки.",
    "остатки": "Проблема с доступностью товара. Проверяются sellable stock, wbStocks, остатки на складах, риск OOS и поставки.",
    "заказы": "Фактическое падение заказов относительно базового периода.",
    "выручка": "Фактическое падение суммы заказов относительно базового периода.",
    "цена": "Возможное влияние цены или скидки. Нужно сравнить цену с конкурентами и проверить акции.",
    "требует проверки": "Недостаточно подтверждённых данных для уверенного вывода. Нужна ручная проверка карточки, рекламы, цены и конкурентов.",
}
REASON_TABLE_HINTS = {
    "конверсия": "проверить фото, цену, отзывы, карточку и конкурентов",
    "реклама": "проверить CTR, CPC, ДРР, ставки и кампании",
    "реклама остановлена": "проверить статус кампании, бюджет и ставки",
    "остатки": "проверить доступность товара и поставки",
    "заказы": "проверить динамику заказов и базовый период",
    "выручка": "проверить заказы, цену и средний чек",
    "цена": "сравнить цену с конкурентами и проверить акции",
    "требует проверки": "данных недостаточно, нужна ручная проверка",
}


CONVERSION_REASONS = {
    "low_conversion",
    "cartcount",
    "carttoorderpercent",
    "addtocartpercent",
    "cartcount падение",
    "carttoorderpercent падение",
    "addtocartpercent падение",
}
ADS_REASONS = {
    "low_ads_ctr",
    "low_ads_cpc",
    "low_ads_drr",
    "ctr",
    "cpc",
    "drr",
    "advertising",
    "ads",
}
ADS_STOPPED_REASONS = {"ads_stopped"}
ORDER_REASONS = {"ordercount", "ordercount падение"}
REVENUE_REASONS = {"ordersum", "ordersum падение"}
STOCK_REASONS = {
    "out_of_stock",
    "wbstocks == 0",
    "stocks",
    "stockstate",
    "sellableoutofstock",
    "realsellablestock",
}
PRICE_REASONS = {"price", "цена", "discount", "saleprice", "sale_price"}


def to_number(value, default=0):
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def first_present(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def reason_value(row):
    return first_present(
        row,
        ["root_cause", "problem_label", "problem_type", "decline_source", "metric"],
        "Не определено",
    )


def _normalize_reason(value):
    return str(value or "").strip().lower()


REASON_PRIORITY = ("stocks", "ads", "conversion", "orders", "revenue", "price", "unknown")


def reason_group(row):
    values = [_normalize_reason(row.get(key)) for key in ("root_cause", "problem_label", "problem_type", "decline_source", "metric")]
    values = [value for value in values if value]
    if any(value in STOCK_REASONS for value in values):
        return "stocks"
    if any(value in ADS_STOPPED_REASONS | ADS_REASONS for value in values):
        return "ads"
    if any(value in CONVERSION_REASONS for value in values):
        return "conversion"
    if any(value in PRICE_REASONS for value in values):
        return "price"
    if any(value in ORDER_REASONS for value in values):
        return "orders"
    if any(value in REVENUE_REASONS for value in values):
        return "revenue"
    return "unknown"


def management_reason(row):
    return REASON_LABELS[reason_group(row)]


def reason_explanation(reason):
    return REASON_EXPLANATIONS.get(str(reason or "").strip().lower(), REASON_EXPLANATIONS["требует проверки"])


def reason_table_hint(reason):
    return REASON_TABLE_HINTS.get(str(reason or "").strip().lower(), REASON_TABLE_HINTS["требует проверки"])


def matches_reason_filter(row, selected_reason):
    if not selected_reason or selected_reason == "Все причины":
        return True
    return management_reason(row) == selected_reason.lower()


def seller_name(row, sellers_by_id):
    seller_id = first_present(row, ["seller_id", "sellerId"])
    return sellers_by_id.get(str(seller_id), f"seller_id={seller_id}" if seller_id else "Без seller_id")


def lost_revenue(row):
    return to_number(
        first_present(
            row,
            [
                "potential_revenue_loss",
                "potentialRevenueLoss",
                "lost_order_sum",
                "lostOrderSum",
                "blocked_revenue_per_day",
                "blockedRevenuePerDay",
            ],
        )
    )


def lost_orders(row):
    return to_number(
        first_present(
            row,
            [
                "potential_orders_loss",
                "potentialOrdersLoss",
                "lost_orders",
                "lostOrders",
                "blocked_orders_per_day",
                "blockedOrdersPerDay",
            ],
        )
    )


def severity_status(rows):
    severities = {str(row.get("severity") or "").lower() for row in rows}
    if "critical" in severities:
        return "критично"
    if "warning" in severities:
        return "требует внимания"
    return "стабильно"


def _reason_priority_index(reason_key):
    return REASON_PRIORITY.index(reason_key) if reason_key in REASON_PRIORITY else len(REASON_PRIORITY)


def _single_sku_loss_rows(rows):
    grouped = {}
    without_nm_id = []
    for row in rows:
        nm_id = first_present(row, ["nm_id", "nmId", "nmID"])
        if nm_id in (None, ""):
            without_nm_id.append(row)
            continue

        key = str(nm_id)
        reason_key = reason_group(row)
        current = grouped.get(key)
        row_revenue = lost_revenue(row)
        row_orders = lost_orders(row)
        if current is None:
            grouped[key] = {
                "row": row,
                "reason_key": reason_key,
                "lost_revenue": row_revenue,
                "lost_orders": row_orders,
            }
            continue

        current["lost_revenue"] = max(current["lost_revenue"], row_revenue)
        current["lost_orders"] = max(current["lost_orders"], row_orders)
        if (
            _reason_priority_index(reason_key) < _reason_priority_index(current["reason_key"])
            or (
                reason_key == current["reason_key"]
                and _problem_priority(row) > _problem_priority(current["row"])
            )
        ):
            current["row"] = row
            current["reason_key"] = reason_key

    records = []
    for nm_id, record in grouped.items():
        records.append({**record, "nm_id": nm_id})
    for row in without_nm_id:
        records.append(
            {
                "row": row,
                "reason_key": reason_group(row),
                "lost_revenue": lost_revenue(row),
                "lost_orders": lost_orders(row),
                "nm_id": None,
            }
        )
    return records


def reason_loss_summary(rows):
    grouped = {}
    for record in _single_sku_loss_rows(rows):
        reason_key = record["reason_key"]
        reason = REASON_LABELS[reason_key] if reason_key in REASON_PRIORITY else REASON_LABELS["unknown"]
        summary = grouped.setdefault(
            reason,
            {
                "reason": reason,
                "lost_revenue": 0,
                "lost_orders": 0,
                "sku_ids": set(),
            },
        )
        summary["lost_revenue"] += record["lost_revenue"]
        summary["lost_orders"] += record["lost_orders"]
        if record["nm_id"] not in (None, ""):
            summary["sku_ids"].add(str(record["nm_id"]))

    total_revenue = sum(summary["lost_revenue"] for summary in grouped.values())
    total_orders = sum(summary["lost_orders"] for summary in grouped.values())
    metric_key = "lost_revenue" if total_revenue > 0 else "lost_orders"
    total_loss = total_revenue if total_revenue > 0 else total_orders

    records = []
    for summary in grouped.values():
        metric_loss = summary[metric_key]
        share = (metric_loss / total_loss * 100) if total_loss else 0
        records.append(
            {
                "reason": summary["reason"],
                "lost_revenue": summary["lost_revenue"],
                "lost_orders": summary["lost_orders"],
                "sku_count": len(summary["sku_ids"]),
                "share": share,
                "metric_key": metric_key,
                "metric_loss": metric_loss,
            }
        )

    return sorted(records, key=lambda record: record["metric_loss"], reverse=True)


def main_reason(rows):
    summary = reason_loss_summary(rows)
    return summary[0]["reason"] if summary else "требует проверки"


def format_money(value):
    return f"{to_number(value):,.0f} ₽".replace(",", " ")


def format_number(value):
    return f"{to_number(value):,.0f}".replace(",", " ")


def _problem_priority(row):
    return (
        lost_revenue(row),
        lost_orders(row),
        to_number(first_present(row, ["businessImpactScore", "business_impact_score"])),
        to_number(first_present(row, ["severityScore", "severity_score"])),
        to_number(first_present(row, ["dynamicPercent", "dynamic_percent"])),
    )


def _deduplicate_top_sku(problems):
    by_nm_id = {}
    without_nm_id = []
    for row in problems:
        nm_id = first_present(row, ["nm_id", "nmId", "nmID"])
        if nm_id in (None, ""):
            without_nm_id.append(row)
            continue
        key = str(nm_id)
        if key not in by_nm_id or _problem_priority(row) > _problem_priority(by_nm_id[key]):
            by_nm_id[key] = row
    return [*by_nm_id.values(), *without_nm_id]


def _group_rows_by_sku(problems):
    grouped = {}
    for row in problems:
        nm_id = first_present(row, ["nm_id", "nmId", "nmID"])
        key = str(nm_id) if nm_id not in (None, "") else f"__row__:{id(row)}"
        grouped.setdefault(key, []).append(row)
    return grouped


def _reason_score(row):
    revenue_loss = lost_revenue(row)
    orders_loss = lost_orders(row)
    impact_score = to_number(first_present(row, ["businessImpactScore", "business_impact_score"]))
    severity_score = to_number(first_present(row, ["severityScore", "severity_score"]))
    dynamic_percent = abs(to_number(first_present(row, ["dynamicPercent", "dynamic_percent"])))
    return revenue_loss or orders_loss or impact_score or severity_score or dynamic_percent or 1


def sku_reason_ranking(rows, limit=3):
    scores = {}
    for row in rows:
        reason_key = reason_group(row)
        if reason_key == "unknown":
            continue
        scores[reason_key] = scores.get(reason_key, 0) + _reason_score(row)

    if not scores:
        return [(REASON_LABELS["unknown"], 100)]

    ranked = sorted(
        scores.items(),
        key=lambda item: (-item[1], _reason_priority_index(item[0])),
    )[:limit]
    total = sum(score for _, score in ranked)
    if total <= 0:
        return [(REASON_LABELS[ranked[0][0]], 100)]

    weights = []
    accumulated = 0
    for index, (reason_key, score) in enumerate(ranked):
        if index == len(ranked) - 1:
            weight = max(100 - accumulated, 0)
        else:
            weight = round(score / total * 100)
            accumulated += weight
        weights.append((REASON_LABELS[reason_key], weight))
    return weights


def sku_diagnosis(rows):
    ranking = sku_reason_ranking(rows)
    lines = [f"{reason.capitalize()} ({weight}%)" for reason, weight in ranking]
    if len(ranking) > 1 and ranking[0][1] - ranking[1][1] < 10:
        return "Причина не определена однозначно\n" + "\n".join(lines)
    return "\n".join(lines)


def sku_main_reason(rows):
    ranking = sku_reason_ranking(rows, limit=1)
    return ranking[0][0] if ranking else REASON_LABELS["unknown"]


def prepare_seller_table(problems, sellers_by_id):
    grouped = {}
    for row in problems:
        seller_id = str(first_present(row, ["seller_id", "sellerId"], ""))
        grouped.setdefault(seller_id, []).append(row)

    records = []
    for seller_id, rows in grouped.items():
        records.append(
            {
                "продавец": sellers_by_id.get(seller_id, f"seller_id={seller_id}" if seller_id else "Без seller_id"),
                "потеря выручки": sum(lost_revenue(row) for row in rows),
                "потеря заказов": round(sum(lost_orders(row) for row in rows)),
                "критичных SKU": len({first_present(row, ["nm_id", "nmId"]) for row in rows if first_present(row, ["nm_id", "nmId"])}),
                "главная причина": main_reason(rows),
                "статус": severity_status(rows),
            }
        )

    return pd.DataFrame(records).sort_values("потеря выручки", ascending=False) if records else pd.DataFrame()


def prepare_sku_table(problems, sellers_by_id):
    records = []
    rows_by_sku = _group_rows_by_sku(problems)
    for row in _deduplicate_top_sku(problems):
        nm_id = first_present(row, ["nm_id", "nmId", "nmID"], "")
        sku_rows = rows_by_sku.get(str(nm_id), [row]) if nm_id not in (None, "") else [row]
        main_sku_reason = sku_main_reason(sku_rows)
        records.append(
            {
                "продавец": seller_name(row, sellers_by_id),
                "артикул WB": nm_id,
                "название": first_present(row, ["title", "productName", "product_name"], ""),
                "потеря выручки": lost_revenue(row),
                "потеря заказов": round(lost_orders(row)),
                "диагноз SKU": sku_diagnosis(sku_rows),
                "пояснение причины": reason_table_hint(main_sku_reason),
                "подтверждение": first_present(row, ["impact_confidence", "report_trust_score", "reportTrustScore"], ""),
                "действие": first_present(row, ["root_recommendation", "recommendation", "forecast_message"], ""),
            }
        )

    return pd.DataFrame(records).sort_values("потеря выручки", ascending=False) if records else pd.DataFrame()
