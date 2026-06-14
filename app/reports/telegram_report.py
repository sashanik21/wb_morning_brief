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
EXECUTIVE_PROBLEMS_LIMIT = 3
TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT = 6
logger = logging.getLogger(__name__)

DECLINE_SOURCE_TELEGRAM_LABELS = {
    "ADS_DECLINE": "ADS",
    "ORGANIC_DECLINE": "ORGANIC",
    "CONVERSION_DECLINE": "CONVERSION",
    "STOCK_DECLINE": "STOCK",
    "MIXED_DECLINE": "MIXED",
    "INSUFFICIENT_DATA": "INSUFFICIENT_DATA",
}

DECLINE_SOURCE_PRIORITY_LABELS = {
    "ADS_DECLINE": "реклама",
    "ORGANIC_DECLINE": "органика",
    "CONVERSION_DECLINE": "конверсия",
    "STOCK_DECLINE": "остатки",
    "MIXED_DECLINE": "смешанный",
    "INSUFFICIENT_DATA": "недостаточно данных",
}


def _is_below_abc_threshold(problem):
    value = problem.get("isBelowAbcThreshold")

    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "да"}

    return bool(value)


def _is_critical_telegram_problem(problem):
    severity = str(problem.get("severity") or "").lower()
    return severity == "critical" and not _is_below_abc_threshold(problem)


def _is_priority_telegram_problem(problem):
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
        product["problems"].sort(key=lambda item: -to_number(item.get("severityScore")))
        product["severityScore"] = to_number(
            product["problems"][0].get("severityScore") if product["problems"] else 0
        )

    return sorted(
        grouped_products.values(),
        key=lambda product: (-product["severityScore"], product["first_index"]),
    )


def _human_readable_problem_type(problem):
    problem_label = str(problem.get("problemLabel") or "").strip()

    if problem_label:
        return problem_label

    metric = str(problem.get("metric") or "").strip()

    if metric:
        return get_problem_label(metric)

    return get_problem_label(problem.get("problemType"))


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
    if metric in {"wbStocks", "stocks"} or "Остатки" in root_zone:
        return "STOCKS"
    if "трафик" in root_zone.lower() or metric in {"openCount"}:
        return "TRAFFIC"
    if metric in {"addToCartPercent", "cartCount"} or "Карточка" in root_zone:
        return "CARD"
    if metric in {"orderCount", "orderSum", "cartToOrderPercent"}:
        return "CONVERSION"

    return "CONVERSION"


def _impact_rank(problem):
    if problem.get("problemType") == "NEW_ACTIVITY_DETECTED":
        return "NEW ACTIVITY"

    score = to_number(problem.get("severityScore"))
    lost_revenue = _problem_lost_revenue(problem)
    abc = str(problem.get("ABC") or "").upper()

    if score >= 80 or lost_revenue >= 10000 or (abc == "A" and score >= 50):
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
    ]

    if problem.get("baselineReliability") == "INSUFFICIENT_HISTORY":
        for label, _past_key, selected_key, suffix in metrics:
            selected_value = problem.get(selected_key)
            if _is_present(selected_value):
                lines.append(f"{label}: {_format_number(selected_value)}{suffix}")
        lines.append("новая рекламная активность")
        return "\n".join(lines)

    for label, past_key, selected_key, suffix in metrics:
        selected_value = problem.get(selected_key)
        past_value = problem.get(past_key)

        if _is_present(selected_value) and _is_present(past_value):
            lines.append(
                f"{label}: {_format_value_change(past_value, selected_value, suffix)}"
            )

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
        lines.append(f"visibility score: {_format_number(visibility_score)}%")

    risk = problem.get("searchVisibilityRisk")
    if _is_present(risk):
        lines.append(f"risk: {html.escape(str(risk))}")

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
    if confidence:
        lines.append(f"confidence: {html.escape(confidence)}")
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
        f"Артикул WB: {nm_id}\n"
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
        f"📦 SKU после ABC-фильтра: <b>{problem_products_count}</b>"
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
        f"SKU из WB API: {_format_number(summary_stats.get('totalSkuFromApi'))}\n"
        f"SKU есть в PRODUCTS: {_format_number(summary_stats.get('skuInProducts'))}\n"
        f"SKU нет в PRODUCTS: {_format_number(summary_stats.get('skuNotInProducts'))}\n"
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

    return f"— {title} (WB {nm_id}): {metric} {dynamic} ({comparison})"


def _build_top_drop_signals_block(summary_stats):
    if not summary_stats:
        return ""

    top_drop_signals = summary_stats.get("topDropSignals") or []

    if not top_drop_signals:
        return "📉 <b>TOP SKU по падению:</b>\n— Просадок по funnel не найдено"

    return "📉 <b>TOP SKU по падению:</b>\n" + "\n".join(
        _format_drop_signal(signal) for signal in top_drop_signals
    )


def _format_money(value):
    return f"{_format_number(value)} ₽"


def _build_ads_block(records, summary_stats):
    ads_summary = (summary_stats or {}).get("adsSummary") or {}
    ads_records = [
        record
        for record in records
        if isinstance(record, dict) and record.get("problemCategory") == "ads"
    ]

    if not ads_summary and not ads_records:
        return ""

    active_campaigns = ads_summary.get("activeCampaigns", 0)
    problem_campaigns = ads_summary.get("problemCampaigns", 0)
    ads_problem_count = ads_summary.get("problems", len(ads_records))
    block_lines = [
        f"📢 <b>Реклама:</b> проблем {_format_number(ads_problem_count)}",
        f"Активных кампаний: <b>{_format_number(active_campaigns)}</b>",
        f"Проблемных кампаний: <b>{_format_number(problem_campaigns)}</b>",
    ]

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


def _build_executive_header(summary_stats):
    summary_stats = summary_stats or {}
    seller_name = html.escape(str(summary_stats.get("sellerName") or SELLER_NAME))

    return f"📊 <b>WB Morning Brief — Executive Summary</b>\nПродавец: <b>{seller_name}</b>"


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
    title = html.escape(str(product.get("title") or "Без названия"))
    nm_id = html.escape(str(product.get("nmId") or "n/a"))

    return f"{title} (WB {nm_id})"


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

    for product in problem_products:
        insight = insights_by_key.get(_problem_group_key(product))

        if insight and insight.get("reason"):
            zone = html.escape(str(insight.get("rootCauseZone") or "причина"))
            reason = html.escape(str(insight.get("reason")))
            return f"🧠 <b>Главный инсайт:</b> {zone}: {reason}"

    if (summary_stats or {}).get("orderCountDynamic", 0) < 0:
        return "🧠 <b>Главный инсайт:</b> просадка заказов требует проверки трафика, конверсии и наличия."

    return "🧠 <b>Главный инсайт:</b> критичный управленческий сигнал не выявлен."


def _build_executive_ads_block(records, summary_stats):
    ads_summary = (summary_stats or {}).get("adsSummary") or {}
    ads_records = [
        record
        for record in records
        if isinstance(record, dict) and record.get("problemCategory") == "ads"
    ]

    if not ads_summary and not ads_records:
        return "📢 <b>Реклама:</b> данных для сигнала нет"

    active_campaigns = ads_summary.get("activeCampaigns", 0)
    problem_campaigns = ads_summary.get(
        "problemCampaigns", ads_summary.get("problems", len(ads_records))
    )

    if not ads_records:
        return (
            "📢 <b>Реклама:</b> "
            f"активных кампаний {_format_number(active_campaigns)}, "
            "критичных проблем нет"
        )

    first_problem = ads_records[0]
    reason = html.escape(
        str(
            first_problem.get("problemLabel")
            or first_problem.get("problemType")
            or "проверить эффективность расходов"
        )
    )
    return (
        f"📢 <b>Реклама:</b> проблем {_format_number(problem_campaigns)}. "
        f"Активных кампаний {_format_number(active_campaigns)}. "
        f"Фокус: {reason}"
    )


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
        return "📦 Логистика WB: данные поставок недоступны"

    return (
        "📦 Логистика WB:"
        f"\n- SKU с supply goods: {_format_number(pipeline['matched'])}"
        f"\n- readyForSale в поставках: {_format_number(pipeline['ready'])} шт"
        f"\n- в приемке: {_format_number(pipeline['acceptance'])} шт"
        f"\n- в разгрузке: {_format_number(pipeline['transit'])} шт"
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
    title = html.escape(str(top_record.get("title") or "SKU без названия"))
    recommendation = html.escape(
        str(top_record.get("recommendation") or "проверить наличие и поставку")
    )

    return (
        "📦 <b>Остатки:</b> "
        f"критичных SKU {_format_number(len(stock_records))}. "
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

    return f"{title} (WB {nm_id}, выручка {dynamic})"


def _build_no_problem_executive_block(summary_stats):
    best, worst = _best_worst_from_evidence(summary_stats)

    return (
        f"Лучший SKU: {_format_signal_sku(best)}\n"
        f"Худший SKU: {_format_signal_sku(worst)}\n"
        "Рекомендации: сохранить текущие настройки, точечно проверить худший SKU "
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
            "Товар есть в supply goods как readyForSale, но не отражается "
            "в sellable stock. Проверить расхождение WB stocks vs supplies."
        )
    if incoming > 0 or acceptance > 0 or transit > 0:
        return "Товар уже находится в логистике WB"

    return (
        "Товар отсутствует в sellable stock. "
        "Данных о поставке или возврате в логистике WB нет."
    )


def _format_stock_stop_block(problem):
    stock_state = str(problem.get("stockState") or "n/a")
    sellable = problem.get("realSellableStock")
    if sellable in (None, ""):
        sellable = problem.get("selectedValue")

    return (
        "\n⚠️ SKU временно недоступен для продажи"
        f"\n{_stock_stop_reason(problem)}"
        "\n📦 Логистика WB:"
        f"\n- состояние: {stock_state}"
        f"\n- sellable stock: {_format_number(sellable)}"
        f"\n- supply readyForSale: {_format_number(problem.get('readyForSaleStock'))}"
        f"\n- incoming: {_format_number(problem.get('incomingStock'))}"
        f"\n- acceptance: {_format_number(problem.get('acceptanceStock'))}"
        f"\n- transit: {_format_number(problem.get('transitStock'))}"
    )


def _format_priority_problem_line(problem):
    nm_id = html.escape(str(problem.get("nmId") or "n/a"))
    problem_label = html.escape(_human_readable_problem_type(problem))
    impact = html.escape(_impact_rank(problem))
    zone = html.escape(_problem_zone(problem))
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

    return (
        f"- <b>{impact}</b> | {zone} | WB {nm_id} | {problem_label}"
        f"{decline_source_block}{specifics_block}\n{html.escape(_format_business_impact(problem))}"
        f"{html.escape(stock_stop)}"
    )


def _build_priority_problems_block(priority_records):
    if not priority_records:
        return "✅ <b>Приоритетных проблем не найдено</b>"

    lines = [
        _format_priority_problem_line(record)
        for record in priority_records[:TELEGRAM_TOP_LIMIT]
    ]
    return "🔥 <b>Приоритетные проблемы:</b>\n" + "\n".join(lines)


def _build_risk_zones_block(priority_records, root_cause_insights):
    if not priority_records:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    sku_by_zone = {}

    for record in priority_records:
        zone = _problem_zone(record, insights_by_key)
        sku_by_zone.setdefault(zone, set()).add(_problem_group_key(record))

    lines = [
        f"{zone}: {_format_number(len(skus))} SKU"
        for zone, skus in sorted(sku_by_zone.items())
    ]
    return "🔥 <b>Главные зоны риска:</b>\n" + "\n".join(lines)


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
        lines.append(f"confidence: {confidence}")
    if len(lines) == 1:
        lines.append("недостаточно данных для точного расчёта")
    return "\n".join(lines)


def _build_top_impact_block(priority_records):
    records = sorted(
        [record for record in priority_records if _problem_lost_revenue(record) > 0],
        key=_problem_lost_revenue,
        reverse=True,
    )[:3]
    if not records:
        return ""
    lines = ["🔥 <b>TOP потерь:</b>"]
    for index, record in enumerate(records, start=1):
        nm_id = html.escape(str(record.get("nmId") or "—"))
        title = html.escape(str(record.get("title") or "").strip())
        name = f"WB {nm_id}" + (f" {title}" if title else "")
        lines.append(
            f"{index}. {name} — ≈ {_format_number(_problem_lost_revenue(record))} ₽"
        )
    return "\n".join(lines)


def _check_priority_score(record):
    stock_bonus = 100 if record.get("metric") == "wbStocks" else 0
    ads_bonus = 30 if _problem_zone(record) == "ADS" else 0
    return (
        to_number(record.get("severityScore"))
        + _problem_lost_revenue(record) / 1000
        + stock_bonus
        + ads_bonus
    )


def _build_first_checks_block(priority_records, root_cause_insights):
    if not priority_records:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    checks = []
    seen = set()

    for record in sorted(priority_records, key=_check_priority_score, reverse=True):
        insight = insights_by_key.get(_problem_group_key(record), {})

        if (
            record.get("metric") == "wbStocks"
            and to_number(record.get("selectedValue")) == 0
        ):
            check = (
                "Восстановить остатки WB: продажи остановлены из-за нулевого склада."
            )
        elif _problem_zone(record, insights_by_key) == "ADS":
            check = "Проверить рекламную эффективность: CTR, CPC, ДРР, бюджет и статус кампании."
        elif insight.get("reason"):
            check = f"{insight.get('rootCauseZone')}: {insight.get('reason')}."
        else:
            check = (
                record.get("recommendation")
                or "Проверить воронку, цену, рекламу и остатки."
            )

        if check in seen:
            continue

        seen.add(check)
        checks.append(html.escape(str(check)))

        if len(checks) == 3:
            break

    lines = [f"{index}. {check}" for index, check in enumerate(checks, start=1)]
    return "🎯 <b>Что проверить в первую очередь:</b>\n" + "\n".join(lines)


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
            f"Артикул WB: {escape(row.get('nmId') or 'n/a')}\n\n"
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


def _build_telegram_message(problems, summary_stats=None, root_cause_insights=None):
    records = _problems_to_records(problems)
    priority_records = [
        record for record in records if _is_priority_telegram_problem(record)
    ]
    low_priority_signals_count = len(
        [record for record in records if _is_low_priority_telegram_problem(record)]
    )
    problem_products = _group_problems_by_product(priority_records)
    priority_sku_count = len(problem_products)
    message_parts = [
        _build_executive_header(summary_stats),
        _build_executive_store_dynamics(summary_stats),
        f"🚨 <b>Приоритетных SKU:</b> {_format_number(priority_sku_count)}",
        f"Низкоприоритетных сигналов: {_format_number(low_priority_signals_count)}",
    ]

    if not priority_records:
        message_parts.extend(
            [
                _build_priority_problems_block(priority_records),
                _build_no_problem_executive_block(summary_stats),
                _build_executive_ads_block(priority_records, summary_stats),
                _build_executive_stocks_block(priority_records, summary_stats),
            ]
        )
        return _trim_telegram_message(
            "\n\n".join(part for part in message_parts if part)
        )

    message_parts.extend(
        [
            _build_risk_zones_block(priority_records, root_cause_insights),
            _build_daily_losses_block(priority_records),
            _build_top_impact_block(priority_records),
            _build_priority_problems_block(priority_records),
            _build_first_checks_block(priority_records, root_cause_insights),
            _build_executive_insight(
                problem_products, root_cause_insights, summary_stats
            ),
            _build_executive_ads_block(priority_records, summary_stats),
            _build_executive_stocks_block(priority_records, summary_stats),
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
