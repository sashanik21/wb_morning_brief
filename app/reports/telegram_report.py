import html
import logging
import os

import requests

from app.analyzers.severity import SEVERITY_LABELS, to_number
from app.constants.problem_labels import get_problem_label
from app.reports.evidence import (
    EVIDENCE_LIMIT_TELEGRAM,
    build_evidence_rows,
    escape,
    format_number,
    format_percent,
)
from app.seller_config import SELLER_NAME

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_TOP_LIMIT = 5
FORECAST_ALERT_LIMIT = 3
LOW_PRIORITY_SIGNAL_THRESHOLD = 10
EXECUTIVE_PROBLEMS_LIMIT = 3
TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT = 6
EXECUTIVE_ACTIONS_LIMIT = 5
logger = logging.getLogger(__name__)

DECLINE_SOURCE_TELEGRAM_LABELS = {
    "ADS_DECLINE": "реклама",
    "ORGANIC_DECLINE": "органика",
    "CONVERSION_DECLINE": "конверсия",
    "STOCK_DECLINE": "остатки",
    "MIXED_DECLINE": "смешанный",
    "INSUFFICIENT_DATA": "недостаточно данных",
}

DECLINE_SOURCE_PRIORITY_LABELS = {
    "ADS_DECLINE": "реклама",
    "ORGANIC_DECLINE": "органика",
    "CONVERSION_DECLINE": "конверсия",
    "STOCK_DECLINE": "остатки",
    "MIXED_DECLINE": "смешанный",
    "INSUFFICIENT_DATA": "недостаточно данных",
}


IMPACT_RANK_LABELS = {
    "HIGH IMPACT": "Высокий риск",
    "MEDIUM IMPACT": "Средний риск",
    "LOW IMPACT": "Низкий риск",
    "IMPACT TBD": "Риск требует проверки",
    "NEW ACTIVITY": "Новая активность",
}

ZONE_LABELS = {
    "AVAILABILITY": "Остатки",
    "STOCKS": "Остатки",
    "CONVERSION": "Конверсия",
    "TRAFFIC": "Трафик",
    "ADS": "Реклама",
    "CARD": "Карточка",
}

TRUST_SCORE_LABELS = {
    "LOW": "низкая — накоплено менее 7 дней истории или не хватает ключевых данных",
    "MEDIUM": "средняя — часть оценок требует проверки",
    "HIGH": "высокая — данных достаточно для управленческих выводов",
}


def _russian_impact_rank(problem):
    return IMPACT_RANK_LABELS.get(_impact_rank(problem), _impact_rank(problem))


def _russian_zone(zone):
    return ZONE_LABELS.get(str(zone or ""), str(zone or ""))


def _format_product_identity(record_or_product):
    title_value = record_or_product.get("title") or "Без названия"
    perfume_line = record_or_product.get("perfumeLine")
    volume_ml = record_or_product.get("volumeMl")
    if perfume_line:
        title_value = f"{perfume_line} {volume_ml} мл" if volume_ml else perfume_line
    title = html.escape(str(title_value))
    nm_id = record_or_product.get("nmId") or record_or_product.get("nm_id")
    if nm_id in (None, ""):
        return title
    return f"{title} — WB {html.escape(str(nm_id))}"


def _format_report_trust_score(score):
    score = str(score or "").upper()
    return TRUST_SCORE_LABELS.get(score, "средняя — часть оценок требует проверки")


def _format_product_count(count):
    count = int(to_number(count) or 0)
    if count % 10 == 1 and count % 100 != 11:
        word = "товар"
    elif count % 10 in {2, 3, 4} and count % 100 not in {12, 13, 14}:
        word = "товара"
    else:
        word = "товаров"
    return f"{_format_number(count)} {word}"


def _is_below_abc_threshold(problem):
    value = problem.get("isBelowAbcThreshold")

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "да"}

    return bool(value)


def _is_critical_telegram_problem(problem):
    severity = str(problem.get("severity") or "").lower()
    return severity == "critical" and not _is_below_abc_threshold(problem)


def _is_priority_telegram_problem(problem):
    if problem.get("isSuppressed") or _is_insufficient_history_problem(problem):
        return False
    action_priority = str(problem.get("actionPriority") or "")
    if action_priority in {"NOW", "TODAY", "THIS_WEEK"}:
        return True
    severity = str(problem.get("severity") or "").lower()
    return severity in {"critical", "high", "medium"} and not _is_below_abc_threshold(
        problem
    )


def _is_low_priority_telegram_problem(problem):
    severity = str(problem.get("severity") or "").lower()
    return severity == "low" or _is_below_abc_threshold(problem)


def _split_long_telegram_line(line, max_length):
    chunks = []
    start = 0

    while start < len(line):
        end = min(start + max_length, len(line))

        if end == len(line):
            chunks.append(line[start:end])
            break

        split_at = None
        inside_tag = False

        for index in range(start, end):
            character = line[index]

            if character == "<":
                inside_tag = True
            elif character == ">":
                inside_tag = False
            elif character.isspace() and not inside_tag:
                split_at = index

        if split_at is None or split_at <= start:
            split_at = end
            last_open_tag = line.rfind("<", start, split_at)
            last_close_tag = line.rfind(">", start, split_at)

            if last_open_tag > last_close_tag:
                split_at = last_open_tag

            if split_at <= start:
                split_at = end

        chunks.append(line[start:split_at].rstrip())
        start = split_at

        while start < len(line) and line[start].isspace():
            start += 1

    return [chunk for chunk in chunks if chunk]


def split_telegram_message(text, max_length=3500):
    if not text:
        return []

    if len(text) <= max_length:
        return [text]

    parts = []
    current_part = ""

    def append_unit(unit):
        nonlocal current_part

        if not unit:
            return

        separator = "\n\n" if current_part else ""

        if len(current_part) + len(separator) + len(unit) <= max_length:
            current_part = f"{current_part}{separator}{unit}"
            return

        if current_part:
            parts.append(current_part)
            current_part = ""

        if len(unit) <= max_length:
            current_part = unit
            return

        append_long_unit(unit)

    def append_long_unit(unit):
        for line in unit.splitlines():
            if len(line) <= max_length:
                append_line(line)
            else:
                for chunk in _split_long_telegram_line(line, max_length):
                    append_line(chunk)

    def append_line(line):
        nonlocal current_part

        separator = "\n" if current_part else ""

        if len(current_part) + len(separator) + len(line) <= max_length:
            current_part = f"{current_part}{separator}{line}"
            return

        if current_part:
            parts.append(current_part)

        current_part = line

    for block in text.split("\n\n"):
        append_unit(block)

    if current_part:
        parts.append(current_part)

    return parts


def _problems_to_records(problems):
    if problems is None:
        return []

    if hasattr(problems, "to_dict"):
        return problems.to_dict("records")

    if isinstance(problems, list):
        return problems

    return []


def _is_present(value):
    return value not in (None, "") and str(value) != "nan"


def _format_dynamic_percent(value):
    if value in (None, ""):
        return "n/a"

    value_text = str(value).strip()
    if value_text.endswith("%"):
        return value_text

    return f"{value_text}%"


def _problem_group_key(problem):
    nm_id = problem.get("nmId")

    if nm_id not in (None, ""):
        return ("nmId", str(nm_id))

    return ("title", str(problem.get("title") or "Без названия"))


def _group_problems_by_product(records):
    grouped_products = {}

    for index, problem in enumerate(records):
        group_key = _problem_group_key(problem)

        if group_key not in grouped_products:
            grouped_products[group_key] = {
                "first_index": index,
                "problems": [],
                "title": problem.get("title") or "Без названия",
                "vendorCode": problem.get("vendorCode") or "n/a",
                "nmId": problem.get("nmId") or "n/a",
                "sellerName": problem.get("sellerName") or SELLER_NAME,
                "ABC": problem.get("ABC") or "n/a",
            }

        grouped_products[group_key]["problems"].append(problem)

    for product in grouped_products.values():
        product["problems"].sort(
            key=lambda item: (
                -to_number(item.get("businessPriorityScore")),
                -to_number(item.get("severityScore")),
            )
        )
        product["severityScore"] = to_number(
            product["problems"][0].get("severityScore") if product["problems"] else 0
        )
        product["businessPriorityScore"] = to_number(
            product["problems"][0].get("businessPriorityScore")
            if product["problems"]
            else 0
        )

    return sorted(
        grouped_products.values(),
        key=lambda product: (
            -product["businessPriorityScore"],
            -product["severityScore"],
            product["first_index"],
        ),
    )


def _human_readable_problem_type(problem):
    problem_label = str(problem.get("problemLabel") or "").strip()
    metric = str(problem.get("metric") or "").strip()
    problem_type = str(problem.get("problemType") or "").strip()

    if problem_label and problem_label.lower() != "n/a":
        if problem_label == "Прогноз риска" and metric.upper() in {
            "STOCK_FORECAST",
            "ADS_FORECAST",
            "ORGANIC_FORECAST",
        }:
            return get_problem_label(metric)
        return problem_label

    if metric:
        label = get_problem_label(metric)
        if label.lower() != "n/a":
            return label

    if problem_type:
        label = get_problem_label(problem_type)
        if label.lower() != "n/a":
            return label

    return "Прогноз риска" if _is_predictive_problem(problem) else "Проблема"


def _format_problem_line(problem):
    problem_type = html.escape(_human_readable_problem_type(problem))

    if problem.get("metric") == "wbStocks" and _is_present(
        problem.get("selectedValue")
    ):
        problem_value = html.escape(str(problem.get("selectedValue")))
    elif problem.get("baselineType") == "avg_3d" and _is_present(
        problem.get("baselineValue")
    ):
        selected_value = html.escape(str(problem.get("selectedValue") or "0"))
        baseline_value = html.escape(str(problem.get("baselineValue") or "0"))
        dynamic = html.escape(_format_dynamic_percent(problem.get("dynamicPercent")))
        problem_value = f"{selected_value} vs avg3d {baseline_value} ({dynamic})"
    else:
        problem_value = html.escape(
            _format_dynamic_percent(problem.get("dynamicPercent"))
        )

    return f"— {problem_type}: {problem_value}"


def _format_severity(problem):
    severity = str(problem.get("severity") or "low").lower()
    emoji = {
        "critical": "🔴",
        "high": "🟠",
        "medium": "🟡",
        "low": "🟢",
    }.get(severity, "🟢")
    label = SEVERITY_LABELS.get(severity, severity.title())
    return f"{emoji} <b>{html.escape(label)}</b>"


def _format_loss(problems):
    lost_orders = sum(_problem_lost_orders(problem) for problem in problems)
    lost_order_sum = sum(_problem_lost_revenue(problem) for problem in problems)

    if not lost_orders and not lost_order_sum:
        return ""

    orders_text = _format_number(lost_orders)
    sum_text = _format_number(lost_order_sum)
    return f"Потеря: <b>{orders_text} заказов / {sum_text} ₽</b>"


def _loss_value(problem, primary_key, fallback_key):
    value = problem.get(primary_key)
    if not _is_present(value):
        value = problem.get(fallback_key)
    if not _is_present(value):
        return None
    return to_number(value)


def _problem_lost_orders(problem):
    return _loss_value(problem, "potentialOrdersLoss", "lostOrders") or 0


def _problem_lost_revenue(problem):
    return _loss_value(problem, "potentialRevenueLoss", "lostOrderSum") or 0


def _problem_blocked_revenue(problem):
    return _loss_value(problem, "blockedRevenuePerDay", "potentialRevenueLoss") or 0


def _problem_impact_value(problem):
    return max(_problem_lost_revenue(problem), _problem_blocked_revenue(problem))


def _is_insufficient_history_problem(problem):
    return (
        problem.get("rootCause") == "INSUFFICIENT_HISTORY"
        or problem.get("baselineReliability") == "INSUFFICIENT_HISTORY"
        or problem.get("impactConfidence") == "INSUFFICIENT_HISTORY"
        or problem.get("problemType") == "INSUFFICIENT_HISTORY"
    )


def _is_oos_blocked_problem(problem):
    sellable = problem.get("realSellableStock")
    if sellable in (None, ""):
        sellable = problem.get("selectedValue")

    return (
        to_number(sellable) == 0
        and str(problem.get("stockState") or "").upper() == "BLOCKED"
        and _decline_source(problem) == "STOCK_DECLINE"
    )


def _is_stock_impact_problem(problem):
    return (
        _is_oos_blocked_problem(problem)
        or _decline_source(problem) == "STOCK_DECLINE"
        or problem.get("problemType") == "sellableOutOfStock"
        or problem.get("metric") in {"wbStocks", "realSellableStock", "stocks"}
        or problem.get("problemCategory") == "stocks"
    )


def _has_problem_loss(problem):
    revenue = _loss_value(problem, "potentialRevenueLoss", "lostOrderSum")
    orders = _loss_value(problem, "potentialOrdersLoss", "lostOrders")
    return (revenue is not None and revenue > 0) or (orders is not None and orders > 0)


def _problem_zone(problem, insights_by_key=None):
    metric = str(problem.get("metric") or "")
    problem_type = str(problem.get("problemType") or "")
    category = str(problem.get("problemCategory") or "")
    insight = (insights_by_key or {}).get(_problem_group_key(problem), {})
    root_zone = str(insight.get("rootCauseZone") or "")

    if category == "ads" or problem_type.startswith("ads_"):
        return "ADS"
    if _is_oos_blocked_problem(problem):
        return "AVAILABILITY"
    if metric in {"wbStocks", "stocks", "realSellableStock"} or "Остатки" in root_zone:
        return "STOCKS"
    if "трафик" in root_zone.lower() or metric in {"openCount"}:
        return "TRAFFIC"
    if metric in {"addToCartPercent", "cartCount"} or "Карточка" in root_zone:
        return "CARD"
    if metric in {"orderCount", "orderSum", "cartToOrderPercent"}:
        return "CONVERSION"

    return "CONVERSION"


def _impact_confidence_reason(problem):
    confidence = _impact_confidence(problem)
    if not confidence:
        return ""
    if confidence == "INSUFFICIENT_HISTORY":
        return "низкая — недостаточно истории (<7 дней)"
    baseline_reliability = str(problem.get("baselineReliability") or "")
    baseline_type = str(problem.get("baselineType") or "")
    if confidence == "LOW" and (
        baseline_reliability == "previous_day"
        or baseline_type in {"previous_day", "prev_day"}
    ):
        return "низкая — сравнение только со вчерашним днем"
    if confidence == "LOW" and baseline_reliability == "INSUFFICIENT_HISTORY":
        return "низкая — недостаточно истории (<7 дней)"
    return confidence


def _impact_rank(problem):
    if problem.get("problemType") == "NEW_ACTIVITY_DETECTED":
        return "NEW ACTIVITY"

    score = to_number(problem.get("severityScore"))
    lost_revenue = _problem_lost_revenue(problem)
    abc = str(problem.get("ABC") or "").upper()
    confidence = _impact_confidence(problem)
    has_loss = _has_problem_loss(problem)
    weak_confidence = confidence in {"LOW", "INSUFFICIENT_HISTORY"}

    if not has_loss and weak_confidence:
        return "IMPACT TBD"
    if (
        score >= 80 or lost_revenue >= 10000 or (abc == "A" and score >= 50)
    ) and not weak_confidence:
        return "HIGH IMPACT"
    if score >= 35 or lost_revenue >= 3000 or abc in {"A", "B"}:
        return "MEDIUM IMPACT"
    return "LOW IMPACT"


def _format_value_change(past_value, selected_value, suffix=""):
    return (
        f"{_format_number(past_value)}{suffix} → "
        f"{_format_number(selected_value)}{suffix}"
    )


def _format_ads_specifics(problem):
    if not (
        problem.get("problemCategory") == "ads"
        or str(problem.get("problemType") or "").startswith("ads_")
    ):
        return ""

    lines = []
    metrics = [
        ("CTR", "previousCtr", "ctr", "%"),
        ("CPC", "previousCpc", "cpc", " ₽"),
        ("ДРР", "previousDrr", "drr", "%"),
        ("Средняя позиция", "previousAvgPosition", "avgPosition", ""),
    ]

    if (
        problem.get("baselineReliability") == "INSUFFICIENT_HISTORY"
        and problem.get("adsRootCause") == "INSUFFICIENT_DATA"
    ):
        for label, _past_key, selected_key, suffix in metrics:
            selected_value = problem.get(selected_key)
            if _is_present(selected_value):
                lines.append(f"{label}: {_format_number(selected_value)}{suffix}")
        lines.append("новая рекламная активность")
    else:
        for label, past_key, selected_key, suffix in metrics:
            selected_value = problem.get(selected_key)
            past_value = problem.get(past_key)

            if _is_present(selected_value) and _is_present(past_value):
                lines.append(
                    f"{label}: {_format_value_change(past_value, selected_value, suffix)}"
                )

    if problem.get("bidDelta") not in (None, ""):
        lines.append(f"Ставка: Δ {_format_number(problem.get('bidDelta'))}%")
    if problem.get("auctionTemperature"):
        temperature = html.escape(str(problem.get("auctionTemperature")))
        lines.append(f"Температура аукциона: {temperature}")

    return "\n".join(lines)


def _format_funnel_specifics(problem):
    metric = str(problem.get("metric") or "")

    if problem.get("problemCategory") == "ads" or metric in {
        "wbStocks",
        "realSellableStock",
    }:
        return ""

    label = get_problem_label(metric)
    selected_value = problem.get("selectedValue")
    past_value = problem.get("pastValue")

    if not (_is_present(selected_value) and _is_present(past_value)):
        return ""

    suffix = " ₽" if metric == "orderSum" else "%"
    if metric in {"orderCount", "openCount", "cartCount"}:
        suffix = ""

    return f"{label}: {_format_value_change(past_value, selected_value, suffix)}"


def _format_visibility_specifics(problem):
    avg_position = problem.get("avgPosition")
    past_position = problem.get("pastAvgPosition") or problem.get("previousAvgPosition")
    visibility_score = problem.get("visibilityScore")

    if not (
        _is_present(avg_position)
        or _is_present(past_position)
        or _is_present(visibility_score)
    ):
        return ""

    lines = ["📉 Видимость:"]
    if _is_present(avg_position) and _is_present(past_position):
        lines.append(
            f"позиция: {_format_number(past_position)} → {_format_number(avg_position)}"
        )
    elif _is_present(avg_position):
        lines.append(f"позиция: {_format_number(avg_position)}")

    if _is_present(visibility_score):
        lines.append(f"индекс видимости: {_format_number(visibility_score)}%")

    risk = problem.get("searchVisibilityRisk")
    if _is_present(risk):
        lines.append(f"риск: {html.escape(str(risk))}")

    return "\n".join(lines)


def _impact_confidence(problem):
    return str(problem.get("impactConfidence") or "").strip()


def _decline_source(problem):
    return str(
        problem.get("declineSource") or problem.get("decline_source") or ""
    ).strip()


def _format_decline_source_line(problem, labels):
    source = _decline_source(problem)

    if not source:
        return ""

    return labels.get(source, source)


def _format_business_impact(problem):
    confidence = _impact_confidence(problem)
    if confidence == "INSUFFICIENT_HISTORY":
        return "потери не рассчитаны: недостаточно истории"
    if not _has_problem_loss(problem):
        return "потери не рассчитаны"

    lines = []
    revenue = _loss_value(problem, "potentialRevenueLoss", "lostOrderSum")
    orders = _loss_value(problem, "potentialOrdersLoss", "lostOrders")
    if revenue is not None and revenue > 0:
        lines.append(f"≈ {_format_number(revenue)} ₽ потери выручки")
    if orders is not None and orders > 0:
        lines.append(f"≈ {_format_number(orders)} потерянных заказов")
    confidence_reason = _impact_confidence_reason(problem)
    if confidence_reason and confidence not in {"LOW", "INSUFFICIENT_HISTORY"}:
        lines.append(f"Надежность оценки: {html.escape(confidence_reason)}")
    return "\n".join(lines) or "потери не рассчитаны"


def _format_recent_changes(problems):
    changes = []
    seen_changes = set()

    for problem in problems:
        recent_changes = problem.get("recentChanges")

        if not _is_present(recent_changes):
            continue

        for change in str(recent_changes).splitlines():
            change = change.strip()

            if not change or change in seen_changes:
                continue

            seen_changes.add(change)
            changes.append(f"— {html.escape(change)}")

    if not changes:
        return ""

    return "\n\n🧩 <b>Последние изменения:</b>\n" + "\n".join(changes)


def _format_recommendations(problems):
    recommendations = []
    seen_recommendations = set()

    for problem in problems:
        recommendation = str(problem.get("recommendation") or "").strip()

        if not recommendation or recommendation in seen_recommendations:
            continue

        seen_recommendations.add(recommendation)
        recommendations.append(html.escape(recommendation))

    if not recommendations:
        return "n/a"

    return "; ".join(recommendations)


def _format_product_item(_index, product):
    title = html.escape(str(product["title"]))
    vendor_code = html.escape(str(product["vendorCode"]))
    nm_id = html.escape(str(product["nmId"]))
    abc = html.escape(str(product.get("ABC") or "n/a"))
    problems = product["problems"]
    problem_lines = [
        _format_problem_line(problem)
        for problem in problems[:TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT]
    ]
    recommendations = _format_recommendations(problems)
    recent_changes = _format_recent_changes(problems)

    primary_problem = problems[0] if problems else {}
    decline_source = _format_decline_source_line(
        primary_problem, DECLINE_SOURCE_TELEGRAM_LABELS
    )
    decline_source_block = (
        f"\n📊 <b>Источник просадки:</b> {html.escape(decline_source)}"
        if decline_source
        else ""
    )
    loss_line = _format_loss(problems)
    loss_block = f"\n{loss_line}" if loss_line else ""

    return (
        f"{_format_severity(primary_problem)}\n"
        f"🏷️ <b>{title}</b>\n\n"
        f"Артикул продавца: {vendor_code}\n"
        f"WB: {nm_id}\n"
        f"ABC: {abc}\n\n"
        f"Проблем: <b>{len(problems)}</b>"
        + loss_block
        + "\n\n"
        + "\n".join(problem_lines)
        + recent_changes
        + f"\n\n💡 <b>Что проверить:</b>\n{recommendations}"
    )


def _build_telegram_header(total_problems, problem_products_count, summary_stats=None):
    summary_stats = summary_stats or {}
    seller_name = html.escape(str(summary_stats.get("sellerName") or SELLER_NAME))

    return (
        "📊 <b>WB Morning Brief</b>\n"
        f"Продавец: <b>{seller_name}</b>\n\n"
        f"Всего проблем: <b>{total_problems}</b>\n"
        f"Проблемных товаров: <b>{problem_products_count}</b>\n"
        f"📦 Товаров после ABC-фильтра: <b>{problem_products_count}</b>"
    )


def _format_number(value):
    if value in (None, ""):
        return "0"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    if number.is_integer():
        return f"{int(number):,}".replace(",", " ")

    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


def _build_summary_block(summary_stats):
    if not summary_stats:
        return ""

    storage = summary_stats.get("storage") or {}
    storage_mode = storage.get("mode") or "stub"

    return (
        "📊 <b>Сводка:</b>\n"
        f"Storage: {html.escape(storage_mode)}\n"
        f"Товаров из WB API: {_format_number(summary_stats.get('totalSkuFromApi'))}\n"
        f"Товаров есть в PRODUCTS: {_format_number(summary_stats.get('skuInProducts'))}\n"
        f"Товаров нет в PRODUCTS: {_format_number(summary_stats.get('skuNotInProducts'))}\n"
        f"Ниже ABC-порога: "
        f"{_format_number(summary_stats.get('belowAbcThresholdProblems'))}\n"
        f"Переходы в карточку: "
        f"{_format_number(summary_stats.get('totalOpenCount'))}\n"
        f"Корзины: {_format_number(summary_stats.get('totalCartCount'))}\n"
        f"Заказы: {_format_number(summary_stats.get('totalOrders'))}\n"
        f"Сумма заказов: {_format_number(summary_stats.get('totalOrderSum'))} ₽"
    )


def _format_dynamic_value(value):
    if value in (None, ""):
        return "n/a"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    return f"{number:.1f}%"


def _format_store_dynamic_line(
    label, selected_value, past_value, dynamic_value, suffix=""
):
    return (
        f"{label}: {_format_number(selected_value)}{suffix} vs "
        f"{_format_number(past_value)}{suffix} ({_format_dynamic_value(dynamic_value)})"
    )


def _build_store_dynamics_block(summary_stats):
    if not summary_stats:
        return ""

    return (
        "📈 <b>Динамика магазина:</b>\n"
        + _format_store_dynamic_line(
            "Переходы",
            summary_stats.get("selectedOpenCount"),
            summary_stats.get("pastOpenCount"),
            summary_stats.get("openCountDynamic"),
        )
        + "\n"
        + _format_store_dynamic_line(
            "Корзины",
            summary_stats.get("selectedCartCount"),
            summary_stats.get("pastCartCount"),
            summary_stats.get("cartCountDynamic"),
        )
        + "\n"
        + _format_store_dynamic_line(
            "Заказы",
            summary_stats.get("selectedOrderCount"),
            summary_stats.get("pastOrderCount"),
            summary_stats.get("orderCountDynamic"),
        )
        + "\n"
        + _format_store_dynamic_line(
            "Сумма заказов",
            summary_stats.get("selectedOrderSum"),
            summary_stats.get("pastOrderSum"),
            summary_stats.get("orderSumDynamic"),
            suffix=" ₽",
        )
    )


def _build_control_signals_block(summary_stats):
    if not summary_stats:
        return ""

    signals = []

    storage = summary_stats.get("storage") or {}

    if storage.get("mode") == "stub":
        signals.append("⚠️ Storage работает в stub-режиме")

    if summary_stats.get("skuNotInProducts", 0) > 0:
        signals.append("⚠️ Есть карточки WB, не внесённые в PRODUCTS")

    if summary_stats.get("belowAbcThresholdProblems", 0) > 0:
        signals.append("⚠️ Есть низкоприоритетные сигналы ниже ABC-порога")

    if summary_stats.get("totalOrders", 0) == 0:
        signals.append("🔴 Заказов нет")

    if summary_stats.get("totalOrderSum", 0) == 0:
        signals.append("🔴 Сумма заказов 0 ₽")

    if summary_stats.get("orderCountDynamic", 0) <= -10:
        signals.append("🔴 Заказы по магазину просели")

    if summary_stats.get("orderSumDynamic", 0) <= -10:
        signals.append("🔴 Сумма заказов по магазину просела")

    if summary_stats.get("openCountDynamic", 0) <= -15:
        signals.append("🔴 Переходы в карточки просели")

    if summary_stats.get("cartCountDynamic", 0) <= -10:
        signals.append("🔴 Корзины просели")

    if not signals:
        signals.append("✅ Критичных контрольных сигналов нет")

    return "🔎 <b>Контрольные сигналы:</b>\n" + "\n".join(signals)


def _format_drop_signal(signal):
    title = html.escape(str(signal.get("title") or "Без названия"))
    nm_id = html.escape(str(signal.get("nmId") or "n/a"))
    metric = html.escape(
        str(signal.get("problemLabel") or get_problem_label(signal.get("metric")))
    )
    dynamic = html.escape(_format_dynamic_percent(signal.get("dynamicPercent")))
    selected_value = html.escape(str(signal.get("selectedValue") or "0"))
    past_value = html.escape(str(signal.get("pastValue") or "0"))

    baseline_type = signal.get("baselineType")
    baseline_label = "avg3d" if baseline_type == "avg_3d" else "baseline"

    if baseline_type == "avg_3d":
        comparison = f"{selected_value} vs {baseline_label} {past_value}"
    else:
        comparison = f"{selected_value} vs {past_value}"

    return f"— {title} — WB {nm_id}: {metric} {dynamic} ({comparison})"


def _build_top_drop_signals_block(summary_stats):
    if not summary_stats:
        return ""

    top_drop_signals = summary_stats.get("topDropSignals") or []

    if not top_drop_signals:
        return "📉 <b>TOP товаров по падению:</b>\n— Просадок по funnel не найдено"

    return "📉 <b>TOP товаров по падению:</b>\n" + "\n".join(
        _format_drop_signal(signal) for signal in top_drop_signals
    )


def _format_money(value):
    return f"{_format_number(value)} ₽"


def _qbiki_status_rank(row):
    return {
        "PROFITABLE_ADS": 0,
        "ADS_NEEDS_CONTROL": 1,
        "UNPROFITABLE_ADS": 2,
        "ADS_PAUSE_IF_OOS": 3,
    }.get(str(row.get("adsProfitabilityStatus") or ""), 9)


def _format_qbiki_conclusion(row):
    status = str(row.get("adsProfitabilityStatus") or "")
    if status == "PROFITABLE_ADS":
        return "реклама прибыльная"
    if status == "ADS_NEEDS_CONTROL":
        if to_number(row.get("wbStock")) == 0:
            return "остановить рекламу до восстановления остатков"
        return (
            "реклама прибыльная, но остатка хватит примерно на "
            + _format_number(row.get("daysOfStock"))
            + " день"
        )
    if status == "UNPROFITABLE_ADS":
        return "реклама убыточная — проверить ставку, стоимость заказа и кампании"
    if status == "ADS_PAUSE_IF_OOS":
        return "товара нет на WB — остановить рекламу до восстановления остатков"
    return "нужно проверить экономику рекламы"


def _format_qbiki_product_line(index, row):
    return (
        f"{index}. <b>{_format_product_identity(row)}</b>\n"
        f"   CTR рекламы: {_format_dynamic_value(row.get('adsCTR'))}\n"
        f"   Стоимость заказа из рекламы: {_format_money(row.get('CPO'))}\n"
        f"   ДРР: {_format_dynamic_value(row.get('DRR'))}\n"
        f"   Чистый ДРР: {_format_dynamic_value(row.get('cleanDRR'))}\n"
        f"   Окупаемость рекламы: {_format_dynamic_value(row.get('ROI'))}\n"
        f"   Вывод: {_format_qbiki_conclusion(row)}."
    )


def _build_qbiki_ads_profitability_block(summary_stats):
    rows = [
        row
        for row in (summary_stats or {}).get("qbikiMetrics") or []
        if isinstance(row, dict) and row.get("adsProfitabilityStatus")
    ]
    if not rows:
        return ""

    profitable = [
        row
        for row in rows
        if row.get("adsProfitabilityStatus") in {"PROFITABLE_ADS", "ADS_NEEDS_CONTROL"}
    ]
    problematic = [
        row
        for row in rows
        if row.get("adsProfitabilityStatus") in {"UNPROFITABLE_ADS", "ADS_PAUSE_IF_OOS"}
    ]
    profitable.sort(
        key=lambda row: (
            -to_number(row.get("ROI")),
            to_number(row.get("daysOfStock")) or 999999,
        )
    )
    problematic.sort(
        key=lambda row: (_qbiki_status_rank(row), to_number(row.get("cleanMarginAds")))
    )

    lines = ["📢 <b>Реклама и прибыльность</b>"]
    if profitable:
        lines.append("\nЛучшие товары по рекламе:")
        lines.extend(
            _format_qbiki_product_line(index, row)
            for index, row in enumerate(profitable[:3], start=1)
        )
    if problematic:
        lines.append("\nПроблемные товары:")
        lines.extend(
            _format_qbiki_product_line(index, row)
            for index, row in enumerate(problematic[:3], start=1)
        )
    if len(lines) == 1:
        lines.append("данных для вывода по прибыльности рекламы недостаточно")
    return "\n".join(lines)


def _ads_history_status(ads_summary):
    return "доступна" if (ads_summary or {}).get("pastPeriod") else "недоступна"


def _qbiki_unavailable_line(summary_stats):
    if (summary_stats or {}).get("qbikiMetrics"):
        return ""
    return "📢 Реклама: данные по прибыльности из Qbiki не подключены."


def _format_ads_metric_transition(previous, current, suffix=""):
    if previous in (None, "") or current in (None, ""):
        return "н/д"

    return f"{_format_number(previous)}{suffix} → {_format_number(current)}{suffix}"


def _ads_summary_lines(ads_summary):
    ads_summary = ads_summary or {}
    if _ads_history_status(ads_summary) != "доступна":
        return [
            "📢 <b>Реклама:</b>",
            "статистика за день получена, но история ещё не накоплена.",
            "Динамику CTR, ставок и ДРР можно будет оценить после 3–7 дней данных.",
        ]

    return [
        "📢 <b>Реклама:</b>",
        "CTR рекламы: "
        + _format_ads_metric_transition(
            ads_summary.get("previousCtr"), ads_summary.get("currentCtr"), "%"
        ),
        "CPC: "
        + _format_ads_metric_transition(
            ads_summary.get("previousCpc"), ads_summary.get("currentCpc"), " ₽"
        ),
        "ДРР: "
        + _format_ads_metric_transition(
            ads_summary.get("previousDrr"), ads_summary.get("currentDrr"), "%"
        ),
        "Средняя ставка: "
        + _format_ads_metric_transition(
            ads_summary.get("previousBid"), ads_summary.get("currentBid"), " ₽"
        ),
    ]


def _build_ads_block(records, summary_stats):
    qbiki_block = _build_qbiki_ads_profitability_block(summary_stats)
    if qbiki_block:
        return qbiki_block

    ads_summary = (summary_stats or {}).get("adsSummary") or {}
    ads_records = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("problemCategory") == "ads"
        and not _is_insufficient_history_problem(record)
        and record.get("problemType") != "NEW_ACTIVITY_DETECTED"
    ]

    if not ads_summary and not ads_records:
        return ""

    active_campaigns = ads_summary.get("activeCampaigns", 0)
    problem_campaigns = ads_summary.get("problemCampaigns", 0)
    ads_problem_count = ads_summary.get("problems", len(ads_records))
    block_lines = _ads_summary_lines(ads_summary)
    block_lines.append(f"проблем рекламы: {_format_number(ads_problem_count)}")
    block_lines.append(f"проблемных кампаний: {_format_number(problem_campaigns)}")
    qbiki_line = _qbiki_unavailable_line(summary_stats)
    if qbiki_line:
        block_lines.append(qbiki_line)

    if not ads_records:
        block_lines.append("✅ Проблем рекламы не найдено")
        return "\n".join(block_lines)

    grouped_campaigns = {}

    for record in ads_records:
        campaign_key = record.get("campaignId") or record.get("title")

        if campaign_key not in grouped_campaigns:
            grouped_campaigns[campaign_key] = record

    problem_lines = []

    for record in list(grouped_campaigns.values())[:3]:
        title = html.escape(
            str(record.get("title") or record.get("campaignName") or "Без названия")
        )
        reason = html.escape(
            str(
                record.get("problemLabel")
                or record.get("problemType")
                or "реклама стала неэффективной"
            )
        )
        problem_lines.append(
            f"— <b>{title}</b>\n"
            f"CTR рекламы: {_format_dynamic_value(record.get('ctr'))}\n"
            f"CPC: {_format_money(record.get('cpc'))}\n"
            f"ДРР: {_format_dynamic_value(record.get('drr'))}\n"
            f"Причина:\n{reason}"
        )

    return (
        "\n".join(block_lines)
        + "\n\n🔴 <b>Проблемы рекламы:</b>\n\n"
        + "\n\n".join(problem_lines)
    )


def _insight_key(insight):
    nm_id = insight.get("nmId")

    if nm_id not in (None, ""):
        return ("nmId", str(nm_id))

    return ("title", str(insight.get("title") or "Без названия"))


def _build_root_cause_insights_block(root_cause_insights, top_products):
    if not root_cause_insights or not top_products:
        return ""

    insights_by_key = {
        _insight_key(insight): insight
        for insight in root_cause_insights
        if isinstance(insight, dict)
    }
    formatted_insights = []

    for product in top_products:
        insight = insights_by_key.get(_problem_group_key(product))

        if not insight:
            continue

        title = html.escape(
            str(insight.get("title") or product.get("title") or "Без названия")
        )
        zone = html.escape(str(insight.get("rootCauseZone") or "Недостаточно данных"))
        reason = html.escape(
            str(insight.get("reason") or "Недостаточно данных для определения причины")
        )
        checks = insight.get("whatToCheck") or []
        checks_text = html.escape(
            ", ".join(str(check) for check in checks) or "проверить карточку вручную"
        )
        formatted_insights.append(
            f"🏷️ <b>{title}</b>\n"
            f"Зона проблемы: {zone}\n"
            f"Почему: {reason}\n"
            f"Проверить: {checks_text}"
        )

    if not formatted_insights:
        return ""

    return "🧠 <b>Возможная причина:</b>\n\n" + "\n\n".join(formatted_insights)


def _format_compact_dynamic(label, selected_value, dynamic_value, suffix=""):
    return (
        f"{label}: <b>{_format_number(selected_value)}{suffix}</b> "
        f"({_format_dynamic_value(dynamic_value)})"
    )


def _baseline_context(summary_stats):
    summary_stats = summary_stats or {}
    baseline_mode = (
        summary_stats.get("baselineMode")
        or summary_stats.get("baselineType")
        or summary_stats.get("comparisonMode")
    )
    if baseline_mode in {"previous_day", "prev_day"}:
        return "сравнение с предыдущим днем"
    if baseline_mode:
        return f"сравнение: {html.escape(str(baseline_mode))}"
    return "сравнение со средним за 7 дней"


def _build_executive_header(summary_stats):
    summary_stats = summary_stats or {}
    seller_name = html.escape(str(summary_stats.get("sellerName") or SELLER_NAME))

    return (
        "📊 <b>WB Morning Brief — Executive Summary</b>"
        f"\nПродавец: <b>{seller_name}</b>"
        "\nПериод: вчера 00:00–24:00 МСК"
        f"\n{_baseline_context(summary_stats)}"
    )


def _build_executive_store_dynamics(summary_stats):
    summary_stats = summary_stats or {}

    return (
        "📈 <b>Динамика магазина</b>\n"
        + _format_compact_dynamic(
            "Заказы",
            summary_stats.get("selectedOrderCount") or summary_stats.get("totalOrders"),
            summary_stats.get("orderCountDynamic"),
        )
        + "\n"
        + _format_compact_dynamic(
            "Выручка",
            summary_stats.get("selectedOrderSum") or summary_stats.get("totalOrderSum"),
            summary_stats.get("orderSumDynamic"),
            suffix=" ₽",
        )
    )


def _build_insights_by_key(root_cause_insights):
    return {
        _insight_key(insight): insight
        for insight in root_cause_insights or []
        if isinstance(insight, dict)
    }


def _product_primary_problem(product):
    problems = product.get("problems") or []

    if not problems:
        return {}

    return problems[0]


def _executive_problem_title(product):
    title_value = product.get("title") or "Без названия"
    perfume_line = product.get("perfumeLine")
    volume_ml = product.get("volumeMl")
    if perfume_line:
        title_value = f"{perfume_line} {volume_ml} мл" if volume_ml else perfume_line
    title = html.escape(str(title_value))
    nm_id = html.escape(str(product.get("nmId") or "n/a"))

    return f"{title} — WB {nm_id}"


def _executive_problem_line(index, product, insights_by_key):
    primary_problem = _product_primary_problem(product)
    insight = insights_by_key.get(_problem_group_key(product)) or {}
    problem = html.escape(_human_readable_problem_type(primary_problem))
    dynamic = html.escape(
        _format_dynamic_percent(primary_problem.get("dynamicPercent"))
    )
    consequence = html.escape(
        str(
            insight.get("reason")
            or primary_problem.get("problemLabel")
            or "есть риск потери заказов и выручки"
        )
    )
    action = html.escape(
        str(
            primary_problem.get("recommendation")
            or ", ".join(str(item) for item in insight.get("whatToCheck") or [])
            or "проверить карточку, цену, рекламу и остатки"
        )
    )

    return (
        f"{index}. <b>{_executive_problem_title(product)}</b>\n"
        f"Проблема: {problem} {dynamic}\n"
        f"↓\nПоследствие: {consequence}\n"
        f"↓\nЧто делать: {action}"
    )


def _format_root_cause_chain(problem):
    chain = str(problem.get("rootCauseChain") or "").strip()
    if not chain:
        return ""
    return chain.replace(" → ", "\n↓\n")


def _business_action_text(problem, insight=None):
    insight = insight or {}
    if _is_stock_impact_problem(problem):
        return f"Срочно восстановить остатки {_format_product_identity(problem)}"
    if _problem_zone(problem) == "ADS":
        return f"Снизить перегрев рекламы / проверить ДРР по {_format_product_identity(problem)}"
    if insight.get("rootCauseZone"):
        return f"Проверить {insight.get('rootCauseZone')} по {_format_product_identity(problem)}"
    return str(
        problem.get("recommendation")
        or f"Проверить ключевой сигнал по {_format_product_identity(problem)}"
    )


def _build_executive_actions_block(priority_records, root_cause_insights):
    actionable = [
        record
        for record in priority_records
        if record.get("actionPriority") in {"NOW", "TODAY", "THIS_WEEK"}
        and not record.get("isSuppressed")
    ]
    if not actionable:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    ordered = sorted(
        actionable,
        key=lambda record: (
            {"NOW": 0, "TODAY": 1, "THIS_WEEK": 2}.get(record.get("actionPriority"), 9),
            -to_number(record.get("businessPriorityScore")),
        ),
    )
    lines = []
    seen = set()
    for record in ordered:
        action = _business_action_text(
            record, insights_by_key.get(_problem_group_key(record))
        )
        if action in seen:
            continue
        seen.add(action)
        impact = _problem_impact_value(record)
        impact_text = (
            f" — блокируется ~{_format_number(impact)} ₽/день" if impact > 0 else ""
        )
        priority = html.escape(str(record.get("actionPriority") or "TODAY"))
        lines.append(
            f"{len(lines) + 1}. [{priority}] {html.escape(action)}{impact_text}."
        )
        if len(lines) == EXECUTIVE_ACTIONS_LIMIT:
            break

    return "✅ <b>Что делать сегодня</b>\n" + "\n".join(lines)


def _build_executive_top_problems(problem_products, root_cause_insights):
    if not problem_products:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    top_products = problem_products[:EXECUTIVE_PROBLEMS_LIMIT]
    lines = [
        _executive_problem_line(index, product, insights_by_key)
        for index, product in enumerate(top_products, start=1)
    ]

    return "🔴 <b>Главные проблемы</b>\n" + "\n\n".join(lines)


def _build_executive_insight(problem_products, root_cause_insights, summary_stats):
    insights_by_key = _build_insights_by_key(root_cause_insights)
    top_problems = [_product_primary_problem(product) for product in problem_products]
    stock_problems = [
        problem
        for problem in top_problems
        if problem
        and _is_stock_impact_problem(problem)
        and _problem_impact_value(problem) > 0
    ]

    if stock_problems:
        sku_count = len(
            {
                str(problem.get("nmId"))
                for problem in stock_problems
                if problem.get("nmId")
            }
        ) or len(stock_problems)
        loss = sum(_problem_impact_value(problem) for problem in stock_problems)
        return (
            "🧠 <b>Главный инсайт:</b> Основной риск магазина — потеря продаж "
            "из-за отсутствия остатков по ключевым SKU. Остатки WB: "
            f"{_format_product_count(sku_count)} без остатков блокируют около "
            f"{_format_number(loss)} ₽ выручки в день."
        )

    for product in problem_products:
        primary_problem = _product_primary_problem(product)
        if _is_insufficient_history_problem(primary_problem) and any(
            _problem_impact_value(problem) > 0
            for problem in top_problems
            if not _is_insufficient_history_problem(problem)
        ):
            continue

        insight = insights_by_key.get(_problem_group_key(product))

        if insight and insight.get("reason"):
            zone = html.escape(str(insight.get("rootCauseZone") or "причина"))
            reason = html.escape(str(insight.get("reason")))
            return f"🧠 <b>Главный инсайт:</b> {zone}: {reason}"

    if (summary_stats or {}).get("orderCountDynamic", 0) < 0:
        return "🧠 <b>Главный инсайт:</b> просадка заказов требует проверки трафика, конверсии и наличия."

    return "🧠 <b>Главный инсайт:</b> критичный управленческий сигнал не выявлен."


def _build_perfume_intelligence_block(summary_stats):
    if not (summary_stats or {}).get("qbikiMetrics"):
        return ""
    perfume = (summary_stats or {}).get("perfumeIntelligence") or {}
    insights = [item for item in perfume.get("insights") or [] if item.get("message")]
    volume_rows = perfume.get("volumeAnalytics") or []
    if not insights and not volume_rows:
        return ""

    lines = ["🧴 <b>Аналитика парфюмерии:</b>"]
    for insight in insights[:3]:
        lines.append(f"- {html.escape(str(insight.get('message')))}")

    if volume_rows:
        best_conversion = max(
            volume_rows, key=lambda row: to_number(row.get("conversion"))
        )
        cheapest_cpc = min(
            volume_rows,
            key=lambda row: to_number(row.get("cpc")) or 999999,
        )
        best_margin = max(volume_rows, key=lambda row: to_number(row.get("margin")))
        lines.append(
            "- Объемы: лучшая конверсия у "
            f"{html.escape(str(best_conversion.get('volumeMl') or 'n/a'))} мл; "
            "самый дешевый CPC у "
            f"{html.escape(str(cheapest_cpc.get('volumeMl') or 'n/a'))} мл; "
            "выше средний чек/маржа у "
            f"{html.escape(str(best_margin.get('volumeMl') or 'n/a'))} мл."
        )

    return "\n".join(lines)


def _build_executive_ads_block(records, summary_stats):
    qbiki_block = _build_qbiki_ads_profitability_block(summary_stats)
    if qbiki_block:
        return qbiki_block

    ads_summary = (summary_stats or {}).get("adsSummary") or {}
    ads_records = [
        record
        for record in records
        if isinstance(record, dict)
        and record.get("problemCategory") == "ads"
        and not _is_insufficient_history_problem(record)
        and record.get("problemType") != "NEW_ACTIVITY_DETECTED"
    ]

    if not ads_summary and not ads_records:
        return "📢 <b>Реклама:</b> данных для сигнала нет"

    active_campaigns = ads_summary.get("activeCampaigns", 0)
    problem_campaigns = ads_summary.get(
        "problemCampaigns", ads_summary.get("problems", len(ads_records))
    )

    if not ads_records:
        lines = _ads_summary_lines(ads_summary)
        qbiki_line = _qbiki_unavailable_line(summary_stats)
        if qbiki_line:
            lines.append(qbiki_line)
        lines.append("критичных проблем нет")
        return "\n".join(lines)

    first_problem = ads_records[0]
    score = ads_summary.get("adsEfficiencyScore")
    temperature = ads_summary.get("auctionTemperature") or first_problem.get(
        "auctionTemperature"
    )
    lines = _ads_summary_lines(ads_summary)
    lines.append(f"проблем: {_format_number(problem_campaigns)}")
    qbiki_line = _qbiki_unavailable_line(summary_stats)
    if qbiki_line:
        lines.append(qbiki_line)
    if score not in (None, "") or temperature:
        bits = []
        if score not in (None, ""):
            bits.append(f"оценка эффективности {_format_number(score)}")
        if temperature:
            bits.append(f"аукцион {html.escape(str(temperature))}")
        lines.append("Статус: " + ", ".join(bits) + ".")

    for label, key in (("Лучший SKU", "bestSku"), ("Худший SKU", "worstSku")):
        sku = ads_summary.get(key) or {}
        if sku:
            lines.append(
                f"{label}: {html.escape(str(sku.get('title') or sku.get('nmId') or '—'))} "
                f"(CTR {_format_number(sku.get('ctr'))}%, CPC {_format_number(sku.get('cpc'))} ₽, "
                f"ДРР {_format_number(sku.get('drr'))}%)."
            )

    overheating = [
        p for p in ads_records if p.get("problemType") == "AUCTION_OVERHEATING"
    ]
    cpc_growth = [p for p in ads_records if p.get("problemType") == "ads_cpc_growth"]
    ctr_drop = [p for p in ads_records if p.get("problemType") == "ads_ctr_drop"]
    waste = [
        p
        for p in ads_records
        if p.get("problemType") in {"ads_spend_without_orders", "ads_query_waste"}
        or p.get("budgetWasteRisk")
    ]
    if overheating:
        lines.append(f"Перегретые кампании: {_format_number(len(overheating))}.")
    if cpc_growth:
        lines.append(f"Рост CPC: {_format_product_identity(cpc_growth[0])}.")
    if ctr_drop:
        lines.append(f"Падение CTR: {_format_product_identity(ctr_drop[0])}.")
    if waste:
        lines.append(
            f"Нерациональный расход: {_format_number(sum(to_number(p.get('spend')) for p in waste))} ₽."
        )

    specifics = _format_ads_specifics(first_problem)
    if specifics:
        lines.append(specifics)
    if first_problem.get("problemType") == "AUCTION_OVERHEATING":
        lines.append("Вывод: аукцион перегрет, повышение ставок не дает роста позиций.")
    return "\n".join(lines)


def _supply_pipeline_from_summary(summary_stats):
    supply_metrics = (summary_stats or {}).get("supplyStockMetrics") or {}
    return {
        "incoming": to_number(supply_metrics.get("incomingStock")),
        "acceptance": to_number(supply_metrics.get("acceptanceStock")),
        "transit": to_number(supply_metrics.get("transitStock")),
        "ready": to_number(supply_metrics.get("readyForSaleStock")),
        "matched": to_number(supply_metrics.get("matchedSkuCount")),
    }


def _format_logistics_pipeline(pipeline):
    if not pipeline or not any(pipeline.values()):
        return (
            "📦 Логистика по аккаунту: поставка или возврат товара в WB не обнаружены."
        )

    return (
        "📦 Логистика по аккаунту:"
        f"\n- Товаров в поставках: {_format_number(pipeline['matched'])}"
        f"\n- Общий доступный остаток по аккаунту: {_format_number(pipeline['ready'])} шт"
        f"\n- В приемке: {_format_number(pipeline['acceptance'])} шт"
        f"\n- В транзите: {_format_number(pipeline['transit'])} шт"
    )


def _build_executive_stocks_block(records, summary_stats=None):
    stock_records = [
        record
        for record in records
        if isinstance(record, dict)
        and (
            record.get("problemCategory") == "stocks"
            or record.get("metric") in ("wbStocks", "realSellableStock", "stocks")
        )
    ]
    summary_pipeline = _supply_pipeline_from_summary(summary_stats)

    if not stock_records:
        logistics_text = _format_logistics_pipeline(summary_pipeline)
        return f"📦 <b>Остатки:</b> критичных сигналов нет\n{logistics_text}"

    records_pipeline = {
        "incoming": sum(
            to_number(record.get("incomingStock")) for record in stock_records
        ),
        "acceptance": sum(
            to_number(record.get("acceptanceStock")) for record in stock_records
        ),
        "transit": sum(
            to_number(record.get("transitStock")) for record in stock_records
        ),
        "ready": sum(
            to_number(record.get("readyForSaleStock")) for record in stock_records
        ),
        "matched": len(
            {str(record.get("nmId")) for record in stock_records if record.get("nmId")}
        ),
    }
    pipeline = summary_pipeline if any(summary_pipeline.values()) else records_pipeline

    top_record = stock_records[0]
    title = html.escape(str(top_record.get("title") or "товар без названия"))
    recommendation = html.escape(
        str(top_record.get("recommendation") or "проверить наличие и поставку")
    )

    return (
        "📦 <b>Остатки:</b> "
        f"критичных товаров {_format_number(len(stock_records))}. "
        f"Фокус: {title} — {recommendation}"
        f"\n{_format_logistics_pipeline(pipeline)}"
    )


def _best_worst_from_evidence(summary_stats):
    rows = [
        row
        for row in (summary_stats or {}).get("evidenceRows") or []
        if isinstance(row, dict)
    ]

    if not rows:
        return None, None

    best = max(rows, key=lambda row: row.get("orderSum_delta") or 0)
    worst = min(rows, key=lambda row: row.get("orderSum_delta") or 0)
    return best, worst


def _format_signal_sku(row):
    if not row:
        return "n/a"

    title = html.escape(str(row.get("title") or "Без названия"))
    nm_id = html.escape(str(row.get("nmId") or "n/a"))
    dynamic = html.escape(format_percent(row.get("orderSum_delta")))

    return f"{title} — WB {nm_id}, выручка {dynamic}"


def _build_no_problem_executive_block(summary_stats):
    best, worst = _best_worst_from_evidence(summary_stats)

    return (
        f"Лучший товар: {_format_signal_sku(best)}\n"
        f"Худший товар: {_format_signal_sku(worst)}\n"
        "Рекомендации: сохранить текущие настройки, точечно проверить худший товар "
        "и не расширять рекламу без контроля ДРР."
    )


def _stock_stop_reason(problem):
    incoming = to_number(problem.get("incomingStock"))
    acceptance = to_number(problem.get("acceptanceStock"))
    transit = to_number(problem.get("transitStock"))
    supply_ready = to_number(problem.get("readyForSaleStock"))
    sellable = to_number(
        problem.get("realSellableStock")
        if problem.get("realSellableStock") not in (None, "")
        else problem.get("selectedValue")
    )

    if supply_ready > 0 and sellable == 0:
        return (
            "Товар есть в поставках как готовый к продаже, но не отражается "
            "в доступном к продаже остатке. Проверить расхождение складов и поставок WB."
        )
    if incoming > 0 or acceptance > 0 or transit > 0:
        return "Товар уже находится в логистике WB"

    return (
        "Товар отсутствует в доступном к продаже остатке. "
        "Данных о поставке или возврате в логистике WB нет."
    )


def _format_stock_stop_block(problem):
    sellable = problem.get("realSellableStock")
    if sellable in (None, ""):
        sellable = problem.get("selectedValue")

    pipeline_values = [
        to_number(problem.get("readyForSaleStock")),
        to_number(problem.get("incomingStock")),
        to_number(problem.get("acceptanceStock")),
        to_number(problem.get("transitStock")),
    ]
    if not any(pipeline_values):
        return (
            "\n⚠️ Товар временно недоступен для продажи"
            f"\n{_stock_stop_reason(problem)}"
            "\nПоставка или возврат товара в WB не обнаружены."
        )

    return (
        "\n⚠️ Товар временно недоступен для продажи"
        f"\n{_stock_stop_reason(problem)}"
        "\n📦 Движение товара:"
        f"\n- Остаток доступный к продаже: {_format_number(sellable)}"
        f"\n- Доступно на складах WB: {_format_number(problem.get('readyForSaleStock'))}"
        f"\n- Ожидается поставка: {_format_number(problem.get('incomingStock'))}"
        f"\n- В приемке: {_format_number(problem.get('acceptanceStock'))}"
        f"\n- В транзите: {_format_number(problem.get('transitStock'))}"
    )


def _is_ads_active(problem):
    status = str(
        problem.get("campaignStatus") or problem.get("adsStatus") or ""
    ).lower()
    has_campaign = bool(
        problem.get("campaignId")
        or problem.get("advertId")
        or problem.get("adsCampaignId")
    )
    clicks = to_number(problem.get("clicks") or problem.get("adsClicks"))
    spend = to_number(
        problem.get("spend") or problem.get("adsSpend") or problem.get("sum")
    )
    return (
        has_campaign
        or status in {"active", "running", "активна"}
        or clicks > 0
        or spend > 0
    )


def _has_budget_waste_risk(problem):
    if str(problem.get("budgetWasteRisk") or "").lower() in {"true", "1", "yes", "да"}:
        return True
    sellable = problem.get("realSellableStock")
    if sellable in (None, ""):
        sellable = problem.get("selectedValue")
    return to_number(sellable) == 0 and _is_ads_active(problem)


def _forecast_eta_hours(problem):
    value = problem.get("forecastEtaHours")
    if value in (None, "") and problem.get("daysUntilOOS") not in (None, ""):
        value = to_number(problem.get("daysUntilOOS")) * 24
    return to_number(value)


def _format_forecast_eta(problem):
    hours = _forecast_eta_hours(problem)
    if hours is None:
        return ""
    if hours < 1:
        return "сегодня"
    if hours < 24:
        return f"≈{round(hours)} ч"
    days = hours / 24
    if days <= 2:
        rounded = round(days)
        return f"≈{rounded} {'день' if rounded == 1 else 'дня'}"
    return f"≈{round(days, 1)} дня"


def _report_trust_score(records):
    if not records:
        return "MEDIUM"
    confidence_rank = {"LOW": 1, "INSUFFICIENT_HISTORY": 1, "MEDIUM": 2, "HIGH": 3}
    ranks = []
    complete = 0
    for record in records:
        confidence = _impact_confidence(record) or record.get("forecastConfidence")
        ranks.append(confidence_rank.get(str(confidence or "").upper(), 2))
        if record.get("selectedValue") not in (None, "") and record.get("metric"):
            complete += 1
    avg_rank = sum(ranks) / len(ranks) if ranks else 2
    completeness = complete / len(records)
    if avg_rank >= 2.6 and completeness >= 0.8:
        return "HIGH"
    if avg_rank <= 1.4 or completeness < 0.5:
        return "LOW"
    return "MEDIUM"


def _format_priority_problem_line(problem):
    nm_id = html.escape(str(problem.get("nmId") or "n/a"))
    problem_label = html.escape(_human_readable_problem_type(problem))
    impact = html.escape(_russian_impact_rank(problem))
    zone = html.escape(_russian_zone(_problem_zone(problem)))
    stock_stop = ""
    if (
        problem.get("metric") in {"wbStocks", "realSellableStock"}
        and to_number(problem.get("selectedValue")) == 0
    ):
        stock_stop = _format_stock_stop_block(problem)

    specifics_parts = [
        _format_ads_specifics(problem) or _format_funnel_specifics(problem),
        _format_visibility_specifics(problem),
    ]
    specifics = "\n".join(part for part in specifics_parts if part)
    specifics_block = f"\n{html.escape(specifics)}" if specifics else ""
    decline_source = _format_decline_source_line(
        problem, DECLINE_SOURCE_PRIORITY_LABELS
    )
    decline_source_block = (
        f"\nисточник: {html.escape(decline_source)}" if decline_source else ""
    )
    decision_block = (
        f"\nприоритет: {html.escape(str(problem.get('actionPriority') or 'MONITOR'))}"
        f" / приоритет {_format_number(problem.get('businessPriorityScore'))}"
        f" / SKU {html.escape(str(problem.get('skuCriticality') or 'support'))}"
    )
    cluster_block = ""
    if problem.get("signalCluster"):
        cluster_block = f"\nсигнал: {html.escape(str(problem.get('signalCluster')))}"
    chain_block = ""
    chain = _format_root_cause_chain(problem)
    if chain:
        chain_block = f"\nцепочка причины:\n{html.escape(chain)}"

    budget_waste = ""
    if _has_budget_waste_risk(problem):
        budget_waste = (
            "\n⚠️ Реклама активна при нулевых остатках → возможен слив бюджета."
            "\nРекомендация: Приостановить или сократить рекламу до восстановления остатков."
        )

    return (
        f"- <b>{impact}</b> | {zone} | {_format_product_identity(problem)} | {problem_label}"
        f"{decision_block}{decline_source_block}{cluster_block}{specifics_block}"
        f"{chain_block}\n{html.escape(_format_business_impact(problem))}"
        f"{html.escape(stock_stop)}{html.escape(budget_waste)}"
    )


def _build_priority_problems_block(priority_records):
    if not priority_records:
        return "✅ <b>Приоритетных проблем не найдено</b>"

    lines = [
        _format_priority_problem_line(record)
        for record in priority_records[:TELEGRAM_TOP_LIMIT]
    ]
    return "🔥 <b>Приоритетные проблемы:</b>\n" + "\n\n──────────\n\n".join(lines)


def _build_risk_zones_block(priority_records, root_cause_insights):
    if not priority_records:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    sku_by_zone = {}

    for record in priority_records:
        zone = _problem_zone(record, insights_by_key)
        sku_by_zone.setdefault(zone, set()).add(_problem_group_key(record))

    lines = [
        f"{_russian_zone(zone)}: {_format_product_count(len(skus))}"
        for zone, skus in sorted(sku_by_zone.items())
    ]
    return (
        "🔥 <b>Главные зоны риска (типы сигналов):</b>\n"
        + "\n".join(lines)
        + "\nℹ️ Товары могут входить в несколько категорий."
    )


def _combined_impact_confidence(records):
    ranks = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    confidences = [
        _impact_confidence(record)
        for record in records
        if _impact_confidence(record) in ranks
    ]
    if not confidences:
        return "INSUFFICIENT_HISTORY"
    return min(confidences, key=lambda confidence: ranks[confidence])


def _build_daily_losses_block(priority_records):
    records_with_loss = [
        record for record in priority_records if _has_problem_loss(record)
    ]

    if not records_with_loss:
        return "💸 <b>Потери за день:</b>\n" "недостаточно данных для точного расчёта"

    lost_revenue = sum(_problem_lost_revenue(record) for record in records_with_loss)
    lost_orders = sum(_problem_lost_orders(record) for record in records_with_loss)
    confidence = _combined_impact_confidence(records_with_loss)

    lines = ["💸 <b>Потери за день:</b>"]
    if lost_revenue > 0:
        lines.append(f"≈ {_format_number(lost_revenue)} ₽ потенциальной потери выручки")
    if lost_orders > 0:
        lines.append(f"≈ {_format_number(lost_orders)} потерянных заказов")
    if confidence != "INSUFFICIENT_HISTORY":
        lines.append(f"Надежность оценки: {_format_report_trust_score(confidence)}")
    if len(lines) == 1:
        lines.append("недостаточно данных для точного расчёта")
    return "\n".join(lines)


def _build_top_impact_block(priority_records):
    records = sorted(
        [record for record in priority_records if _problem_impact_value(record) > 0],
        key=_problem_impact_value,
        reverse=True,
    )[:3]
    if not records:
        return ""
    lines = ["🔥 <b>TOP потерь:</b>"]
    for index, record in enumerate(records, start=1):
        nm_id = html.escape(str(record.get("nmId") or "—"))
        title = html.escape(str(record.get("title") or "").strip())
        name = f"{title} — WB {nm_id}" if title else f"WB {nm_id}"
        lines.append(
            f"{index}. {name} — ≈ {_format_number(_problem_impact_value(record))} ₽"
        )
    return "\n".join(lines)


def _check_priority_score(record):
    stock_bonus = 100 if _is_stock_impact_problem(record) else 0
    ads_bonus = 30 if _problem_zone(record) == "ADS" else 0
    history_penalty = 200 if _is_insufficient_history_problem(record) else 0
    return (
        to_number(record.get("severityScore"))
        + _problem_impact_value(record) / 1000
        + stock_bonus
        + ads_bonus
        - history_penalty
    )


def _build_first_checks_block(priority_records, root_cause_insights):
    if not priority_records:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    top_impact_records = sorted(
        [record for record in priority_records if _problem_impact_value(record) > 0],
        key=_problem_impact_value,
        reverse=True,
    )
    stock_impact_records = [
        record for record in top_impact_records if _is_stock_impact_problem(record)
    ]
    checks = []
    seen = set()

    if stock_impact_records:
        sku_list = ", ".join(
            f"{record.get('title') or 'Товар'} — WB {record.get('nmId')}"
            for record in stock_impact_records[:3]
            if record.get("nmId")
        )
        if sku_list:
            check = (
                "Срочно пополнить остатки по товарам с заблокированной выручкой: "
                f"{sku_list}."
            )
            checks.append(html.escape(check))
            seen.add(check)

    has_oos_with_ads = any(
        _is_oos_blocked_problem(record)
        and (
            record.get("campaignId")
            or record.get("advertId")
            or record.get("adsCampaignId")
            or record.get("problemCategory") == "ads"
        )
        for record in priority_records
    )
    if has_oos_with_ads:
        check = (
            "Приостановить или сократить рекламный трафик на товары без остатков "
            "и перераспределить бюджет."
        )
        checks.append(html.escape(check))
        seen.add(check)

    has_stock_or_high_impact = bool(stock_impact_records) or bool(top_impact_records)

    for record in sorted(priority_records, key=_check_priority_score, reverse=True):
        if has_stock_or_high_impact and _is_insufficient_history_problem(record):
            continue

        insight = insights_by_key.get(_problem_group_key(record), {})

        if _is_stock_impact_problem(record):
            check = (
                "Восстановить остатки WB: продажи остановлены из-за нулевого склада."
            )
        elif _problem_zone(record, insights_by_key) == "ADS":
            check = "Проверить рекламную эффективность: CTR, CPC, ДРР, бюджет и статус кампании."
        elif insight.get("reason"):
            check = f"{insight.get('rootCauseZone')}: {insight.get('reason')}."
        else:
            check = (
                record.get("rootCause")
                or record.get("rootRecommendation")
                or record.get("recommendation")
                or "Проверить воронку, цену, рекламу и остатки."
            )

        if check in seen:
            continue

        seen.add(check)
        checks.append(html.escape(str(check)))

        if len(checks) == 3:
            break

    lines = [f"{index}. {check}" for index, check in enumerate(checks, start=1)]
    technical_note = ""
    if any(_is_insufficient_history_problem(record) for record in priority_records):
        technical_note = "\nℹ️ Ограничение анализа: по рекламе пока недостаточно истории (<7 дней данных)."

    return (
        "🎯 <b>Что проверить в первую очередь:</b>\n"
        + "\n".join(lines)
        + technical_note
    )


def _is_predictive_problem(problem):
    return problem.get("forecastType") or str(
        problem.get("problemType") or ""
    ).endswith("_FORECAST")


def _format_forecast_alert(problem):
    forecast_type = str(problem.get("forecastType") or "").upper()
    message = str(problem.get("forecastMessage") or "").strip()

    if message:
        message = message.replace("⚠️ ", "")
        if "SKU может уйти в OOS" in message:
            eta = _format_forecast_eta(problem)
            suffix = f", остатков хватит примерно на {eta}" if eta else ""
            return f"- {_format_product_identity(problem)}: товар может закончиться{suffix}"
        if "nmID" not in message and "SKU" not in message:
            return f"- {_format_product_identity(problem)}: {html.escape(message)}"

    if forecast_type == "OOS" or problem.get("daysUntilOOS") not in (None, ""):
        eta = (
            _format_forecast_eta(problem)
            or f"≈{_format_number(problem.get('daysUntilOOS'))} дня"
        )
        return f"- {_format_product_identity(problem)}: риск обнуления остатков, хватит примерно на {eta}"
    if forecast_type == "ADS":
        return f"- {_format_product_identity(problem)}: риск роста ДРР"
    if forecast_type == "ORGANIC":
        return f"- {_format_product_identity(problem)}: риск падения органики"
    return f"- {_format_product_identity(problem)}: прогнозный риск"


def _build_forecast_risks_block(records):
    forecast_records = [
        record
        for record in records
        if _is_predictive_problem(record)
        and not _is_insufficient_history_problem(record)
        and (
            _forecast_eta_hours(record) is not None
            or record.get("daysUntilOOS") not in (None, "")
            or str(record.get("forecastMessage") or "").strip()
        )
    ]
    if not forecast_records:
        return ""

    forecast_records = sorted(
        forecast_records,
        key=lambda item: (
            _forecast_eta_hours(item) is None,
            _forecast_eta_hours(item) or 9999,
            -_problem_impact_value(item),
        ),
    )
    lines = [
        _format_forecast_alert(record)
        for record in forecast_records[:FORECAST_ALERT_LIMIT]
    ]
    return "🔮 <b>Прогноз рисков:</b>\n" + "\n".join(lines)


def _build_stock_eta_block(records):
    stock_forecasts = [
        record
        for record in records
        if record.get("daysUntilOOS") not in (None, "")
        or str(record.get("forecastType") or "").upper() == "OOS"
    ]
    if not stock_forecasts:
        return ""

    stock_forecasts = sorted(
        stock_forecasts, key=lambda item: _forecast_eta_hours(item) or 9999
    )
    lines = []
    for record in stock_forecasts[:FORECAST_ALERT_LIMIT]:
        eta = (
            _format_forecast_eta(record)
            or f"≈{_format_number(record.get('daysUntilOOS'))} дня"
        )
        lines.append(
            f"- {_format_product_identity(record)}: остатков хватит примерно на {eta}"
        )
    return "📦 <b>Прогноз остатков:</b>\n" + "\n".join(lines)


def _trim_telegram_message(text, max_length=3500):
    if len(text) <= max_length:
        return text

    suffix = "\n\n…сокращено до executive-summary лимита."
    return text[: max_length - len(suffix)].rstrip() + suffix


def _format_evidence_metric(row, label, metric, suffix=""):
    selected_value = format_number(row.get(f"{metric}_selected"))
    past_value = format_number(row.get(f"{metric}_past"))
    dynamic_value = format_percent(row.get(f"{metric}_delta"))

    return f"{label}:\nсейчас {selected_value}{suffix} / было {past_value}{suffix} → {dynamic_value}"


def _telegram_evidence_conclusion(row):
    orders_delta = row.get("orderCount_delta")
    opens_delta = row.get("openCount_delta")
    carts_delta = row.get("cartCount_delta")

    if (
        orders_delta is not None
        and orders_delta < 0
        and opens_delta is not None
        and opens_delta < 0
        and carts_delta is not None
        and carts_delta < 0
    ):
        return "просадка заказов подтверждается падением переходов и корзин."

    return row.get("diagnosis") or "Требует проверки"


def _evidence_footer():
    return (
        "Формула динамики:\n"
        + "(сейчас - было) / было × 100%\n\n"
        + "Пример:\n"
        + "(17 - 30) / 30 × 100% = -43%\n\n"
        + "Источник данных:\n"
        + "WB API sales funnel, selectedPeriod vs pastPeriod."
    )


def _build_evidence_block(summary_stats):
    summary_stats = summary_stats or {}
    evidence_rows = summary_stats.get("evidenceRows")

    if evidence_rows is None:
        evidence_rows = build_evidence_rows(
            summary_stats.get("funnelData"), limit=EVIDENCE_LIMIT_TELEGRAM
        )

    logger.info("EVIDENCE BLOCK: top evidence rows: %s", len(evidence_rows))
    print("EVIDENCE BLOCK:")
    print(f"top evidence rows: {len(evidence_rows)}")

    if not evidence_rows:
        return (
            "📌 <b>Подтверждение по ключевым просадкам:</b>\n"
            "— Просадок заказов/выручки не найдено\n\n" + _evidence_footer()
        )

    formatted_rows = []

    for row in evidence_rows[:EVIDENCE_LIMIT_TELEGRAM]:
        formatted_rows.append(
            f"🏷️ <b>{escape(row.get('title') or 'Без названия')}</b>\n"
            f"Артикул продавца: {escape(row.get('vendorCode') or 'n/a')}\n"
            f"WB: {escape(row.get('nmId') or 'n/a')}\n\n"
            + _format_evidence_metric(row, "Переходы", "openCount")
            + "\n\n"
            + _format_evidence_metric(row, "Корзины", "cartCount")
            + "\n\n"
            + _format_evidence_metric(row, "Заказы", "orderCount")
            + "\n\n"
            + _format_evidence_metric(row, "Выручка", "orderSum", suffix=" ₽")
            + "\n\nВывод:\n"
            + escape(_telegram_evidence_conclusion(row))
        )

    return (
        "📌 <b>Подтверждение по ключевым просадкам:</b>\n\n"
        + "\n\n".join(formatted_rows)
        + "\n\n"
        + _evidence_footer()
    )


def _low_priority_signal_bucket(record):
    zone = _problem_zone(record)
    if zone == "ADS":
        return "реклама"
    if zone in {"STOCKS", "AVAILABILITY"}:
        return "остатки"
    if zone == "CONVERSION":
        return "конверсия"
    if zone in {"TRAFFIC", "CARD"}:
        return "позиции"
    return "прочее"


def _build_low_priority_signals_block(records):
    low_priority_records = [
        record for record in records if _is_low_priority_telegram_problem(record)
    ]
    if not low_priority_records:
        return ""

    buckets = {"реклама": 0, "позиции": 0, "остатки": 0, "конверсия": 0}
    for record in low_priority_records:
        bucket = _low_priority_signal_bucket(record)
        if bucket in buckets:
            buckets[bucket] += 1

    noisy_buckets = [
        bucket
        for bucket, count in buckets.items()
        if count > LOW_PRIORITY_SIGNAL_THRESHOLD
    ]
    if not noisy_buckets:
        return ""
    return (
        "Замечены слабые сигналы ухудшения по нескольким товарам: "
        + ", ".join(noisy_buckets)
        + "."
    )


def _build_telegram_message(problems, summary_stats=None, root_cause_insights=None):
    records = _problems_to_records(problems)
    records = sorted(
        records,
        key=lambda record: (
            record.get("isSuppressed") is True,
            -to_number(record.get("businessPriorityScore")),
            -to_number(record.get("severityScore")),
        ),
    )
    priority_records = [
        record for record in records if _is_priority_telegram_problem(record)
    ]
    problem_products = _group_problems_by_product(priority_records)
    priority_sku_count = len(problem_products)
    trust_score = _report_trust_score(records)
    message_parts = [
        _build_executive_header(summary_stats),
        _build_executive_store_dynamics(summary_stats),
        f"Надежность оценки: {html.escape(_format_report_trust_score(trust_score))}",
        f"🚨 <b>Приоритетных товаров:</b> {_format_number(priority_sku_count)}",
        _build_low_priority_signals_block(records),
        _build_forecast_risks_block(records),
        _build_stock_eta_block(records),
    ]

    if not priority_records:
        message_parts.extend(
            [
                _build_priority_problems_block(priority_records),
                _build_no_problem_executive_block(summary_stats),
                _build_perfume_intelligence_block(summary_stats),
                _build_executive_ads_block(priority_records, summary_stats),
                _build_executive_stocks_block(priority_records, summary_stats),
            ]
        )
        return _trim_telegram_message(
            "\n\n".join(part for part in message_parts if part)
        )

    message_parts.extend(
        [
            _build_executive_insight(
                problem_products, root_cause_insights, summary_stats
            ),
            _build_perfume_intelligence_block(summary_stats),
            _build_executive_actions_block(priority_records, root_cause_insights),
            _build_executive_top_problems(problem_products, root_cause_insights),
            _build_executive_stocks_block(priority_records, summary_stats),
            _build_executive_ads_block(priority_records, summary_stats),
        ]
    )

    return _trim_telegram_message("\n\n".join(part for part in message_parts if part))


def send_telegram_morning_brief(problems, summary_stats=None, root_cause_insights=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not configured")
        return False

    message = _build_telegram_message(
        problems,
        summary_stats=summary_stats,
        root_cause_insights=root_cause_insights,
    )
    url = TELEGRAM_API_URL.format(token=token)
    message_parts = split_telegram_message(message)
    total_parts = len(message_parts)

    for part_index, message_part in enumerate(message_parts, start=1):
        payload = {
            "chat_id": chat_id,
            "text": message_part,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(
                url,
                json=payload,
                timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            logger.error(
                "Telegram text brief part %s/%s request failed: %s",
                part_index,
                total_parts,
                error,
            )
            print(
                f"Telegram text brief part {part_index}/{total_parts} "
                f"request failed: {error}"
            )
            return False

        if response.status_code != 200:
            logger.error(
                "Telegram text brief part %s/%s API error: status=%s text=%s",
                part_index,
                total_parts,
                response.status_code,
                response.text,
            )
            print(
                f"Telegram text brief part {part_index}/{total_parts} API error: "
                f"status={response.status_code} text={response.text}"
            )
            return False

        try:
            data = response.json()
        except ValueError:
            logger.error(
                "Telegram text brief part %s/%s returned invalid JSON: %s",
                part_index,
                total_parts,
                response.text,
            )
            print(
                f"Telegram text brief part {part_index}/{total_parts} "
                "returned invalid JSON"
            )
            return False

        if not data.get("ok"):
            logger.error(
                "Telegram text brief part %s/%s returned error payload: %s",
                part_index,
                total_parts,
                data,
            )
            print(
                f"Telegram text brief part {part_index}/{total_parts} "
                f"returned error: {data}"
            )
            return False

        logger.info(
            "Telegram text brief part %s/%s sent successfully",
            part_index,
            total_parts,
        )
        print(f"Telegram text brief part {part_index}/{total_parts} sent successfully")

    logger.info("Telegram Morning Brief sent successfully")
    print("Telegram Morning Brief sent successfully")
    return True
