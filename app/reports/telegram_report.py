import html
import json
import logging
import re
import os
from datetime import date, datetime

import requests

from app.analyzers.business_ranking import (
    business_ranking_key,
    log_business_ranking,
)
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


TOP_DROP_METRICS = (
    ("orderCount", "Заказы"),
    ("orderSum", "Выручка"),
    ("openCount", "Переходы"),
    ("cartCount", "Корзина"),
    ("cartToOrderPercent", "Конверсия в заказ"),
)

FACTUAL_EXECUTIVE_METRICS = {
    "orderSum",
    "orderCount",
    "openCount",
    "cartCount",
    "addToCartPercent",
    "cartToOrderPercent",
}
FACTUAL_EXECUTIVE_TYPES = {"sellableOutOfStock"}
FORECAST_SIGNAL_TYPES = {"STOCK_FORECAST", "ADS_FORECAST", "ORGANIC_FORECAST"}
logger = logging.getLogger(__name__)


def sanitize_telegram_text(text):
    if text is None:
        return ""

    text = str(text).replace("<1%", "менее 1%")
    allowed_tags = {"a", "b"}
    protected_tags = {}

    def protect_allowed_tag(match):
        tag_name = match.group(1).lower()
        if tag_name not in allowed_tags:
            return match.group(0).replace("<", "‹").replace(">", "›")
        token = f"__TELEGRAM_ALLOWED_TAG_{len(protected_tags)}__"
        protected_tags[token] = match.group(0)
        return token

    text = re.sub(r"</?([A-Za-z][A-Za-z0-9]*)\b[^<>]*>", protect_allowed_tag, text)
    text = re.sub(r"&(?!#\d+;|#x[0-9A-Fa-f]+;|amp;|lt;|gt;|quot;)", "&amp;", text)
    text = text.replace("<", "‹").replace(">", "›")

    for token, tag in protected_tags.items():
        text = text.replace(token, tag)

    return text


def _format_wb_nm_id(nm_id, missing="n/a"):
    if nm_id in (None, "") or str(nm_id) == "n/a":
        return html.escape(str(missing))

    escaped_nm_id = html.escape(str(nm_id))
    href_nm_id = html.escape(str(nm_id), quote=True)
    return (
        f'<a href="https://www.wildberries.ru/catalog/{href_nm_id}/detail.aspx">'
        f"{escaped_nm_id}</a>"
    )


def _format_wb_label(nm_id, missing="n/a"):
    return f"WB {_format_wb_nm_id(nm_id, missing=missing)}"


BUSINESS_METRIC_PRIORITY = {
    "orderSum": 0,
    "orderCount": 1,
    "cartToOrderPercent": 2,
    "openCount": 3,
    "cartCount": 4,
    "addToCartPercent": 4,
}
ADS_METRIC_PRIORITY = {"ctr": 5, "cpc": 5, "drr": 5, "bid": 5}
FORECAST_PRIORITY = 6


def _metric_priority_rank(problem):
    metric = str(problem.get("metric") or "")
    problem_type = str(problem.get("problemType") or "")
    if metric in BUSINESS_METRIC_PRIORITY:
        return BUSINESS_METRIC_PRIORITY[metric]
    if problem.get("problemCategory") == "ads" or problem_type.startswith("ads_"):
        return ADS_METRIC_PRIORITY.get(metric, 6)
    if _is_forecast_signal(problem):
        return FORECAST_PRIORITY
    return 7


def _is_actual_drop(problem):
    selected = problem.get("selectedValue")
    past = problem.get("pastValue")
    if _is_present(selected) and _is_present(past):
        return to_number(selected) < to_number(past)
    dynamic = problem.get("dynamicPercent")
    return _is_present(dynamic) and to_number(dynamic) < 0


def _absolute_metric_drop(problem):
    selected = problem.get("selectedValue")
    past = problem.get("pastValue")
    if not (_is_present(selected) and _is_present(past)):
        return 0
    return max(to_number(past) - to_number(selected), 0)


def _is_ads_problem(record):
    problem_type = str(record.get("problemType") or "")
    return record.get("problemCategory") == "ads" or problem_type.startswith("ads_")


def _is_funnel_problem(record):
    return not _is_ads_problem(record) and not _is_stock_impact_problem(record)


def _is_stock_allowed_above_funnel(record):
    eta_hours = _forecast_eta_hours(record)
    return (eta_hours is not None and eta_hours <= 24) or _problem_blocked_revenue(
        record
    ) > 0


def _business_impact_score(record):
    return to_number(record.get("businessImpactScore"))


def _business_sort_key(record, has_positive_funnel_problem=False):
    source_penalty = 0
    if has_positive_funnel_problem:
        if _is_ads_problem(record):
            source_penalty = 1
        elif _is_stock_impact_problem(record) and not _is_stock_allowed_above_funnel(
            record
        ):
            source_penalty = 1

    return (
        _is_below_abc_threshold(record),
        source_penalty,
        -_business_impact_score(record),
        -to_number(record.get("severityScore")),
        to_number(record.get("dynamicPercent")),
    )


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
    title = html.escape(str(title_value))
    nm_id = record_or_product.get("nmId") or record_or_product.get("nm_id")
    if nm_id in (None, ""):
        return title
    return f"{title} — {_format_wb_label(nm_id)}"


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
    return severity == "critical" and _is_telegram_critical_block_problem(problem)


def _is_telegram_critical_block_problem(problem):
    return not _is_below_abc_threshold(problem)


def _is_forecast_signal(problem):
    metric = str(problem.get("metric") or "").upper()
    problem_type = str(problem.get("problemType") or "").upper()
    root_cause = str(problem.get("rootCause") or "").upper()
    return (
        metric in FORECAST_SIGNAL_TYPES
        or problem_type in FORECAST_SIGNAL_TYPES
        or root_cause in FORECAST_SIGNAL_TYPES
        or bool(problem.get("forecastType"))
        or problem_type.endswith("_FORECAST")
    )


def _has_clear_forecast_payload(problem):
    if not _is_forecast_signal(problem):
        return False
    message = str(
        problem.get("forecastMessage") or problem.get("message") or ""
    ).strip()
    title = str(problem.get("title") or "").strip()
    action = str(problem.get("recommendation") or problem.get("action") or "").strip()
    return bool(
        message
        and message.lower() != "n/a"
        and title
        and title.lower() != "n/a"
        and action
        and action.lower() != "n/a"
        and _problem_impact_value(problem) > 0
    )


def _is_factual_executive_problem(problem):
    if problem.get("isSuppressed"):
        return False
    if _is_insufficient_history_problem(problem) and (
        problem.get("problemCategory") == "ads"
        or str(problem.get("problemType") or "").startswith("ads_")
    ):
        return False
    if _is_forecast_signal(problem):
        return False
    metric = str(problem.get("metric") or "")
    problem_type = str(problem.get("problemType") or "")
    category = str(problem.get("problemCategory") or "")
    if metric in FACTUAL_EXECUTIVE_METRICS or problem_type in FACTUAL_EXECUTIVE_TYPES:
        return _is_actual_drop(problem) or metric in {"wbStocks", "realSellableStock"}
    if category == "ads" or problem_type.startswith("ads_"):
        return _format_ads_specifics(problem) != ""
    return False


def _is_priority_telegram_problem(problem):
    if not _is_factual_executive_problem(problem):
        return False
    action_priority = str(problem.get("actionPriority") or "")
    if action_priority in {"NOW", "TODAY", "THIS_WEEK"}:
        return True
    severity = str(problem.get("severity") or "").lower()
    return severity in {"critical", "high", "medium"}


def _priority_sku_count(records):
    priority_like = [
        record
        for record in records
        if _is_factual_executive_problem(record)
        and not _is_insufficient_history_problem(record)
        and str(record.get("severity") or "").lower() in {"critical", "high", "medium"}
    ]
    return len(_group_problems_by_product(priority_like))


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
        has_positive_funnel_problem = any(
            _is_funnel_problem(problem) and _business_impact_score(problem) > 0
            for problem in product["problems"]
        )
        product["problems"].sort(
            key=lambda problem: _business_sort_key(problem, has_positive_funnel_problem)
        )
        product["severityScore"] = to_number(
            product["problems"][0].get("severityScore") if product["problems"] else 0
        )
        product["businessImpactScore"] = max(
            (_business_impact_score(problem) for problem in product["problems"]),
            default=0,
        )
        product["businessPriorityScore"] = to_number(
            product["problems"][0].get("businessPriorityScore")
            if product["problems"]
            else 0
        )

    has_positive_funnel_product = any(
        any(
            _is_funnel_problem(problem) and _business_impact_score(problem) > 0
            for problem in product.get("problems") or []
        )
        for product in grouped_products.values()
    )
    return sorted(
        grouped_products.values(),
        key=lambda product: (
            business_ranking_key(
                _product_primary_problem(product), has_positive_funnel_product
            ),
            product["first_index"],
        ),
    )


def _unique_nmid_count(records):
    return len(
        {
            str(record.get("nmId"))
            for record in records or []
            if record.get("nmId") not in (None, "", "n/a")
        }
    )


def _product_lost_revenue(product):
    return sum(
        _problem_lost_revenue(problem) for problem in product.get("problems") or []
    )


def _product_lost_orders(product):
    return sum(
        _problem_lost_orders(problem) for problem in product.get("problems") or []
    )


def _product_impact_value(product):
    return sum(
        _problem_impact_value(problem) for problem in product.get("problems") or []
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
        if problem_label == "Прогноз риска" and _is_predictive_problem(problem):
            return "Риск скорого окончания остатков"
        return problem_label

    if metric:
        label = get_problem_label(metric)
        if label.lower() != "n/a":
            return label

    if problem_type:
        label = get_problem_label(problem_type)
        if label.lower() != "n/a":
            return label

    return (
        "Риск скорого окончания остатков"
        if _is_predictive_problem(problem)
        else "Проблема"
    )


def _has_executive_problem_text(problem):
    if _is_insufficient_history_problem(problem):
        return False
    if _is_forecast_signal(problem):
        return _has_clear_forecast_payload(problem)
    problem_label = str(problem.get("problemLabel") or "").strip().lower()
    forecast_message = str(problem.get("forecastMessage") or "").strip().lower()

    if problem_label in {"", "n/a"} and forecast_message in {"", "n/a"}:
        return False
    return True


def _format_ads_problem_line(problem):
    metric_labels = {
        "ctr": "CTR рекламы",
        "cpc": "CPC рекламы",
        "drr": "ДРР рекламы",
        "bid": "Ставка рекламы",
    }
    metric = str(problem.get("metric") or "")
    label = metric_labels.get(metric)
    if not label:
        return ""
    selected = problem.get("selectedValue")
    past = problem.get("pastValue")
    dynamic = problem.get("dynamicPercent")
    suffix = "%" if metric in {"ctr", "drr"} else " ₽" if metric == "cpc" else "%"
    if _is_present(selected) and _is_present(past):
        return (
            f"— {label}: {_format_value_change(past, selected, suffix)} "
            f"({_format_dynamic_value(dynamic)})"
        )
    direction = "снизился" if to_number(dynamic) < 0 else "вырос"
    return f"— {label} {direction} на {html.escape(str(abs(to_number(dynamic))))}%"


def _format_problem_line(problem):
    ads_line = _format_ads_problem_line(problem)
    if ads_line:
        return ads_line

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
            if label == "Средняя позиция" and (not _is_present(selected_value) or to_number(selected_value) <= 0):
                continue
            if _is_present(selected_value):
                lines.append(f"{label}: {_format_number(selected_value)}{suffix}")
        lines.append("новая рекламная активность")
    else:
        for label, past_key, selected_key, suffix in metrics:
            selected_value = problem.get(selected_key)
            past_value = problem.get(past_key)

            if label == "Средняя позиция" and (not _is_present(selected_value) or to_number(selected_value) <= 0):
                continue
            if _is_present(selected_value) and _is_present(past_value):
                past_number = to_number(past_value)
                dynamic = (
                    (to_number(selected_value) - past_number) / past_number * 100
                    if past_number
                    else 0
                )
                if label in {"CTR", "CPC", "ДРР"} and abs(dynamic) < 3:
                    lines.append(f"{label} рекламы без существенных изменений.")
                else:
                    lines.append(
                        f"{label}: {_format_value_change(past_value, selected_value, suffix)}"
                    )

    if problem.get("bidDelta") not in (None, ""):
        lines.append(f"Ставка: Δ {_format_number(problem.get('bidDelta'))}%")
    if problem.get("auctionTemperature"):
        raw_temperature = str(problem.get("auctionTemperature"))
        temperature_label = {
            "NORMAL": "без признаков перегрева",
            "HOT": "аукцион нагрет",
            "OVERHEATED": "аукцион перегрет",
        }.get(raw_temperature.upper(), raw_temperature)
        temperature = html.escape(temperature_label)
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
    nm_id = _format_wb_nm_id(product["nmId"])
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


def _format_percent_one_decimal(value):
    number = to_number(value)
    if value is None:
        return "нет данных"
    if number <= 0:
        return "0%"
    if number < 1:
        return "менее 1%"
    return f"{number:.1f}".replace(".", ",") + "%"


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
    nm_id = _format_wb_nm_id(signal.get("nmId"))
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


def _ads_api_429_limitation_line(summary_stats):
    if _has_partial_ads_data(summary_stats):
        return "⚠️ Данные рекламы частичные."
    return ""


def _has_partial_ads_data(summary_stats=None, ads_summary=None):
    summary_stats = summary_stats or {}
    ads_summary = ads_summary or summary_stats.get("adsSummary") or {}
    rate_limit = summary_stats.get("adsRateLimit") or {}
    if (
        summary_stats.get("adsApiHad429")
        or summary_stats.get("hasApi429")
        or summary_stats.get("adsApiPartial")
        or ads_summary.get("adsApiHad429")
        or ads_summary.get("hasApi429")
        or ads_summary.get("adsApiPartial")
    ):
        return True

    processed = (
        rate_limit.get("campaigns_success")
        or rate_limit.get("campaigns_loaded")
        or (ads_summary or {}).get("campaignsSuccess")
        or (ads_summary or {}).get("processedCampaigns")
    )
    total = (
        rate_limit.get("campaigns_attempted")
        or rate_limit.get("campaigns_requested")
        or rate_limit.get("campaigns_selected")
        or rate_limit.get("campaigns_total")
        or (ads_summary or {}).get("activeCampaigns")
    )
    if processed in (None, "") or total in (None, ""):
        return False
    return to_number(processed) < to_number(total)


def _build_qbiki_ads_profitability_block(summary_stats):
    limitation_line = _ads_api_429_limitation_line(summary_stats)
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
    if limitation_line:
        lines.append(limitation_line)
    return "\n".join(lines)


def _ads_history_status(ads_summary):
    return "доступна" if (ads_summary or {}).get("pastPeriod") else "недоступна"


def _qbiki_unavailable_line(summary_stats):
    return ""


def _ads_baseline_missing(value):
    return value in (None, "") or to_number(value) <= 0


def _ads_has_incomplete_metric_history(ads_summary):
    ads_summary = ads_summary or {}
    if _ads_history_status(ads_summary) != "доступна":
        return True
    return any(
        _ads_baseline_missing(ads_summary.get(key))
        for key in ("previousCtr", "previousCpc", "previousDrr")
    )


def _format_ads_current_metrics(ads_summary):
    ads_summary = ads_summary or {}
    return (
        "Текущие показатели: CTR рекламы "
        f"{_format_number(ads_summary.get('currentCtr'))}%, стоимость клика "
        f"{_format_number(ads_summary.get('currentCpc'))} ₽, ДРР "
        f"{_format_number(ads_summary.get('currentDrr'))}%."
    )


def _format_ads_metric_transition(previous, current, suffix=""):
    if previous in (None, "") or current in (None, ""):
        return "н/д"

    if to_number(previous) == to_number(current):
        return f"{_format_number(current)}{suffix}"

    return f"{_format_number(previous)}{suffix} → {_format_number(current)}{suffix}"


def _metric_change_state(previous, current, stable_threshold=5):
    if previous in (None, "") or current in (None, "") or to_number(previous) == 0:
        return "unknown"
    delta = (to_number(current) - to_number(previous)) / to_number(previous) * 100
    if abs(delta) <= stable_threshold:
        return "stable"
    return "up" if delta > 0 else "down"


def _ads_summary_conclusion(ads_summary):
    ads_summary = ads_summary or {}
    if ads_summary.get("adsCoverageConfidence") == "LOW":
        return "Критичных изменений по рекламе не видно."
    if _ads_has_incomplete_metric_history(ads_summary):
        return "История рекламы пока короткая, выводы предварительные."
    ctr = _metric_change_state(
        ads_summary.get("previousCtr"), ads_summary.get("currentCtr")
    )
    cpc = _metric_change_state(
        ads_summary.get("previousCpc"), ads_summary.get("currentCpc")
    )
    drr = _metric_change_state(
        ads_summary.get("previousDrr"), ads_summary.get("currentDrr")
    )
    if ctr == "stable" and cpc == "up" and drr == "up":
        return (
            "Реклама стала дороже:\n"
            "— стоимость клика выросла;\n"
            "— ДРР увеличился;\n"
            "— CTR существенно не изменился."
        )
    if ctr == "up" and cpc == "stable" and drr == "down":
        return (
            "Эффективность рекламы улучшилась:\n"
            "— CTR вырос;\n"
            "— стоимость привлечения стабильна;\n"
            "— ДРР снизился."
        )
    parts = []
    parts.append(
        "CTR вырос"
        if ctr == "up"
        else "CTR снизился" if ctr == "down" else "CTR существенно не изменился"
    )
    parts.append(
        "стоимость клика выросла"
        if cpc == "up"
        else (
            "стоимость клика снизилась"
            if cpc == "down"
            else "стоимость клика стабильна"
        )
    )
    parts.append(
        "ДРР увеличился"
        if drr == "up"
        else "ДРР снизился" if drr == "down" else "ДРР существенно не изменился"
    )
    return "Вывод по рекламе: " + "; ".join(parts) + "."


def _ads_rows_count(summary_stats=None, ads_summary=None):
    ads_rows_count = (ads_summary or {}).get("adsRows")
    if ads_rows_count in (None, ""):
        ads_rows_count = ((summary_stats or {}).get("adsSummary") or {}).get("adsRows")
    if ads_rows_count in (None, ""):
        ads_rows_count = len((summary_stats or {}).get("adsRows") or [])
    return to_number(ads_rows_count)


def _ads_campaigns_coverage_line(summary_stats=None, ads_summary=None):
    if _has_partial_ads_data(summary_stats, ads_summary):
        return "⚠️ Данные рекламы частичные."
    return None


def _ads_campaigns_success_zero(summary_stats=None, ads_summary=None):
    rate_limit = (summary_stats or {}).get("adsRateLimit") or {}
    success = rate_limit.get("campaigns_success")
    if success in (None, ""):
        success = (ads_summary or {}).get("campaignsSuccess")
    if success in (None, ""):
        return False
    attempted = (
        rate_limit.get("campaigns_attempted")
        or rate_limit.get("campaigns_requested")
        or rate_limit.get("campaigns_selected")
        or rate_limit.get("campaigns_total")
        or (ads_summary or {}).get("activeCampaigns")
    )
    return to_number(success) == 0 and to_number(attempted) > 0


def _ads_processed_campaigns_count(summary_stats=None, ads_summary=None):
    rate_limit = (summary_stats or {}).get("adsRateLimit") or {}
    value = (
        rate_limit.get("campaigns_success")
        or rate_limit.get("campaigns_loaded")
        or (ads_summary or {}).get("campaignsSuccess")
        or (ads_summary or {}).get("processedCampaigns")
        or (ads_summary or {}).get("campaigns")
    )
    return to_number(value)


def _ads_list_values(value):
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple, set)):
        raw_values = value
    else:
        raw_values = str(value).replace(";", ",").split(",")
    return [str(item).strip() for item in raw_values if str(item).strip()]


def _ads_row_campaign_ids(row):
    values = []
    for key in ("campaignId", "advertId", "campaign_id", "campaignIds", "campaign_ids"):
        for value in _ads_list_values((row or {}).get(key)):
            if value not in values:
                values.append(value)
    return values


def _ads_row_campaign_types(row):
    values = []
    for label in _resolve_ads_row_campaign_types(row):
        if label not in values:
            values.append(label)
    return values


def _ads_row_product_title(row):
    for key in (
        "title",
        "productName",
        "product_name",
        "name",
        "subject",
        "campaignName",
    ):
        value = (row or {}).get(key)
        if value not in (None, ""):
            return html.escape(str(value))
    nm_id = (row or {}).get("nmId") or (row or {}).get("nm_id")
    if nm_id not in (None, ""):
        return _format_wb_label(nm_id)
    return "Без названия"


def _first_present_ads_value(row, keys):
    for key in keys:
        value = (row or {}).get(key)
        if _is_present(value):
            return value
    return None


def _ads_row_data_date(row):
    return _first_present_ads_value(row, ("date", "report_date", "reportDate"))


def _format_ads_bid_delta(value):
    delta = to_number(value)
    if not delta:
        return None

    sign = "+" if delta > 0 else "-"
    return f"{sign}{_format_money(abs(delta))}"


def _ads_cart_value(totals):
    for key in ("carts", "cartCount", "addToCart", "addToCartCount"):
        if _is_present((totals or {}).get(key)):
            return to_number(totals.get(key))
    return None


def _ads_previous_cart_value(totals):
    for key in (
        "previous_carts",
        "previous_cartCount",
        "previous_addToCart",
        "previous_addToCartCount",
    ):
        if _is_present((totals or {}).get(key)):
            return to_number(totals.get(key))
    return None


def _ads_any_bid_delta(totals):
    deltas = []
    for key in ("search_bid_delta", "recommendations_bid_delta"):
        if _is_present((totals or {}).get(key)):
            deltas.append(to_number(totals.get(key)))
    change = _product_bid_change(totals or {})
    if change and _is_present(change.get("delta")):
        deltas.append(to_number(change.get("delta")))
    if not deltas:
        return None
    return max(deltas, key=abs)



def _ads_comparison_label(totals):
    status = (totals or {}).get("ads_history_status")
    if status == "avg3":
        return "со средним за 3 дня"
    if status == "previous_day":
        return "с предыдущим доступным днём"
    return "история ещё накапливается"


def _ads_diagnosis_confidence(totals):
    impressions = to_number((totals or {}).get("impressions"))
    clicks = to_number((totals or {}).get("clicks"))
    if impressions > 3000 and clicks > 50:
        return "Высокая"
    if impressions > 500 and clicks > 10:
        return "Средняя"
    return "Низкая"


def _ads_has_enough_diagnosis_data(totals):
    impressions = to_number((totals or {}).get("impressions"))
    clicks = to_number((totals or {}).get("clicks"))
    return not (impressions < 500 and clicks < 20)


def _ads_problem_source(totals, status=None, ads_traffic_share=None):
    if not _ads_has_enough_diagnosis_data(totals):
        if ads_traffic_share is not None and ads_traffic_share < 10:
            return "🟡 Органика"
        return "🟡 Недостаточно данных"
    ctr = to_number((totals or {}).get("ctr"))
    impressions = to_number((totals or {}).get("impressions"))
    clicks = to_number((totals or {}).get("clicks"))
    carts = _ads_cart_value(totals or {})
    if status == "red" or (impressions >= 1000 and ctr < 0.5):
        return "🔴 Реклама"
    if clicks > 0 and carts is not None and carts / clicks * 100 < 5:
        return "🟡 Конверсия"
    return "🟡 Органика"


def _ads_bid_history_ready(summary_stats):
    rows = _ads_bid_delta_rows(summary_stats)
    if not rows:
        return False
    for ads_row in (summary_stats or {}).get("adsRows") or []:
        if not isinstance(ads_row, dict):
            continue
        analytics = ads_row.get("adsBidAnalytics") or {}
        unique_dates = analytics.get("unique_dates_count") or ads_row.get("adsBidHistoryUniqueDates")
        if unique_dates not in (None, "") and to_number(unique_dates) >= 2:
            return True
    return any(row.get("has_previous_bid_history") for row in rows)

def _ads_product_diagnosis(
    totals,
    orders_dynamic=None,
    ads_traffic_share=None,
    open_count=None,
    use_bid_history=False,
):
    totals = totals or {}
    if not totals:
        return {
            "status": "yellow",
            "reason": "🟡 Недостаточно данных",
            "source": "🟡 Недостаточно данных",
            "confidence": "Низкая",
            "confirmation": ["Недостаточно рекламных данных для уверенного вывода."],
            "conclusion": "Причина просадки определена предварительно.",
        }

    clicks = to_number(totals.get("clicks"))
    impressions = to_number(totals.get("impressions"))
    ctr = to_number(totals.get("ctr"))
    drr = to_number(totals.get("drr"))
    orders = to_number(totals.get("orders"))
    previous_clicks = totals.get("previous_clicks")
    previous_impressions = totals.get("previous_impressions")
    previous_ctr = totals.get("previous_ctr")
    previous_cpc = totals.get("previous_cpc")
    previous_drr = totals.get("previous_drr")
    previous_orders = totals.get("previous_orders")
    carts = _ads_cart_value(totals)
    previous_carts = _ads_previous_cart_value(totals)
    bid_delta = _ads_any_bid_delta(totals) if use_bid_history else None
    impressions_dynamic = _ads_metric_dynamic(totals, "impressions")
    clicks_dynamic = _ads_metric_dynamic(totals, "clicks")
    ctr_dynamic = _ads_metric_dynamic(totals, "ctr")
    orders_dynamic_ads = _ads_metric_dynamic(totals, "orders")
    confidence = _ads_diagnosis_confidence(totals)

    if not _ads_has_enough_diagnosis_data(totals):
        confirmation = ["Недостаточно рекламных данных для уверенного вывода."]
        conclusion = "Причина просадки определена предварительно."
        if ads_traffic_share is not None and ads_traffic_share < 10:
            confirmation = [
                "Реклама даёт слишком мало трафика для объяснения просадки."
            ]
            conclusion = "Основное падение произошло вне рекламного канала."
        return {
            "status": "yellow",
            "reason": "🟡 Недостаточно данных",
            "source": _ads_problem_source(totals, ads_traffic_share=ads_traffic_share),
            "confidence": confidence,
            "confirmation": confirmation,
            "conclusion": conclusion,
        }

    impressions_down_20 = (
        _is_present(previous_impressions)
        and to_number(previous_impressions) > 0
        and impressions < to_number(previous_impressions) * 0.8
    )
    clicks_down_20 = (
        _is_present(previous_clicks)
        and to_number(previous_clicks) > 0
        and clicks < to_number(previous_clicks) * 0.8
    )
    ctr_down_20 = (
        _is_present(previous_ctr)
        and to_number(previous_ctr) > 0
        and ctr < to_number(previous_ctr) * 0.8
    )
    cpc_up_20 = (
        _is_present(previous_cpc)
        and to_number(previous_cpc) > 0
        and to_number(totals.get("cpc")) > to_number(previous_cpc) * 1.2
    )
    drr_up_20 = (
        _is_present(previous_drr)
        and to_number(previous_drr) > 0
        and drr > to_number(previous_drr) * 1.2
    )
    impressions_grew = impressions_dynamic is not None and impressions_dynamic > 0
    clicks_stable_or_growing = (
        not _is_present(previous_clicks) or clicks >= to_number(previous_clicks)
    )
    orders_fell = (
        (orders_dynamic is not None and orders_dynamic < 0)
        or (
            _is_present(previous_orders)
            and to_number(previous_orders) > 0
            and orders < to_number(previous_orders)
        )
    )

    if bid_delta is not None and bid_delta < 0 and impressions_down_20:
        return {
            "status": "red",
            "reason": "🔴 Ставка рекламы",
            "confirmation": ["Ставка была снижена, после этого рекламные показы просели."],
            "conclusion": "Снижение ставки могло привести к потере рекламного охвата.",
        }
    if clicks == 0 and impressions > 0:
        return {
            "status": "red",
            "reason": "🔴 Карточка / ставка / релевантность",
            "confirmation": [
                f"Реклама получила {_format_number(impressions)} показов, но не дала переходов."
            ],
            "conclusion": "Покупатель видит товар, но не переходит. Проверить главное фото, цену, ставку, позицию и релевантность показов.",
        }
    if impressions >= 1000 and ctr < 0.5:
        return {
            "status": "red",
            "reason": "🔴 Карточка / цена / позиция",
            "confirmation": [
                f"Реклама получила {_format_number(impressions)} показов, но CTR всего {_format_number(ctr)}%."
            ],
            "conclusion": "Реклама показывает товар, но покупатель не кликает. Проверить главное фото, цену, рейтинг, отзывы и позицию товара.",
        }
    if impressions_down_20:
        return {
            "status": "red",
            "reason": "🔴 Рекламный охват",
            "confirmation": ["Показы рекламы снизились более чем на 20%."],
            "conclusion": "Просадка может быть связана с потерей рекламного охвата. Проверить ставку, зоны показов, кластеры и статус кампании.",
        }
    if clicks_down_20:
        return {
            "status": "red",
            "reason": "🔴 Рекламный трафик",
            "confirmation": [
                f"Клики рекламы снизились на {_format_number(abs(clicks_dynamic or 0))}% относительно {_ads_comparison_label(totals)}."
            ],
            "conclusion": "Просадка связана с потерей рекламного трафика. Проверить ставку, позицию, карточку и релевантность показов.",
        }
    if ctr_down_20:
        return {
            "status": "red",
            "reason": "🔴 Кликабельность карточки",
            "confirmation": ["CTR рекламы снизился более чем на 20% относительно базового периода."],
            "conclusion": "Покупатели хуже кликают по рекламным показам. Проверить главное фото, цену, рейтинг и релевантность.",
        }
    if cpc_up_20:
        return {
            "status": "red",
            "reason": "🔴 Стоимость клика",
            "confirmation": ["CPC вырос более чем на 20% относительно базового периода."],
            "conclusion": "Рекламный клик стал дороже. Проверить ставки, конкуренцию и эффективность кампаний.",
        }
    if drr_up_20:
        return {
            "status": "red",
            "reason": "🔴 ДРР",
            "confirmation": ["ДРР вырос более чем на 20% относительно базового периода."],
            "conclusion": "Реклама стала менее окупаемой. Проверить ставки, CPC, карточку и конверсию в заказ.",
        }
    if bid_delta is not None and bid_delta > 0 and impressions_grew:
        if (ctr_dynamic is not None and ctr_dynamic <= 0) or (
            clicks_dynamic is not None and clicks_dynamic <= 0
        ):
            return {
                "status": "red",
                "reason": "🔴 Карточка / цена / позиция",
                "confirmation": ["Ставка была повышена и увеличила рекламный охват."],
                "conclusion": "Ставка увеличила охват, но не улучшила кликабельность. Проверить главное фото, цену, позицию и релевантность.",
            }
        if clicks_dynamic is not None and clicks_dynamic > 0 and (
            orders_dynamic_ads is not None and orders_dynamic_ads <= 0
        ):
            return {
                "status": "yellow",
                "reason": "🟡 Карточка / цена / конверсия",
                "confirmation": ["Ставка была повышена и дала рекламный трафик."],
                "conclusion": "Ставка дала трафик, но не дала заказы. Проверить карточку, цену и конверсию.",
            }
        return {
            "status": "green",
            "reason": "🟢 Реклама работает стабильно",
            "confirmation": ["Ставка была повышена и рекламные показы выросли."],
            "conclusion": "Ставка была повышена и дала дополнительный рекламный охват.",
        }
    if clicks_stable_or_growing and orders_fell:
        return {
            "status": "yellow",
            "reason": "🟡 Карточка / цена / конверсия",
            "confirmation": ["Рекламные клики не просели, но заказы товара снизились."],
            "conclusion": "Реклама даёт трафик, проблема вероятнее в карточке, цене, доставке или конверсии.",
        }
    if clicks > 0 and carts is not None:
        cart_cr = carts / clicks * 100
        previous_cart_cr = None
        if previous_carts is not None and _is_present(previous_clicks) and to_number(previous_clicks) > 0:
            previous_cart_cr = previous_carts / to_number(previous_clicks) * 100
        if cart_cr < 5 or (previous_cart_cr is not None and cart_cr < previous_cart_cr * 0.8):
            return {
                "status": "yellow",
                "reason": "🟡 Карточка / цена",
                "confirmation": ["Пользователи переходят по рекламе, но плохо добавляют товар в корзину."],
                "conclusion": "Проверить цену, фото, описание, отзывы и УТП.",
            }
    if carts is not None and carts > 0 and _is_present(totals.get("orders")):
        cart_to_order_cr = orders / carts * 100
        previous_cart_to_order_cr = None
        if previous_carts is not None and previous_carts > 0 and _is_present(previous_orders):
            previous_cart_to_order_cr = to_number(previous_orders) / previous_carts * 100
        if previous_cart_to_order_cr is not None and cart_to_order_cr < previous_cart_to_order_cr * 0.8:
            return {
                "status": "yellow",
                "reason": "🟡 Цена / доставка / остатки",
                "confirmation": ["Товар добавляют в корзину, но хуже оформляют заказ."],
                "conclusion": "Проверить цену, сроки доставки, остатки и условия акции.",
            }
    if ads_traffic_share is not None and ads_traffic_share < 10:
        confirmation = [
            f"Реклама дала {_format_number(clicks)} кликов из {_format_number(open_count)} переходов."
            if open_count is not None and open_count > 0
            else f"Реклама дала {_format_number(clicks)} кликов."
        ]
        confirmation.append(
            f"Доля рекламы в переходах: {_format_percent_one_decimal(ads_traffic_share)}."
        )
        return {
            "status": "yellow",
            "reason": "🟡 Проблема вероятнее не в рекламе",
            "confirmation": confirmation,
            "conclusion": "Реклама не выглядит главной причиной просадки. Нужно проверять органику, карточки, цену, конверсию и остатки.",
        }

    clicks_stable = not _is_present(previous_clicks) or clicks >= to_number(previous_clicks) * 0.8
    impressions_stable = not _is_present(previous_impressions) or impressions >= to_number(previous_impressions) * 0.8
    drr_stable = not _is_present(previous_drr) or to_number(previous_drr) <= 0 or drr <= to_number(previous_drr) * 1.2
    if impressions_stable and clicks_stable and drr_stable:
        return {
            "status": "green",
            "reason": "🟢 Реклама работает стабильно",
            "confirmation": ["Рекламные показатели не показывают критичной просадки."],
            "conclusion": "По доступным данным рекламная воронка работает стабильно.",
        }
    return {
        "status": "yellow",
        "reason": "🟡 Проблема вероятнее не в рекламе",
        "confirmation": ["Рекламные данные не подтверждают прямую рекламную причину просадки."],
        "conclusion": "Реклама не выглядит главной причиной просадки. Нужно проверять органику, карточки, цену, конверсию и остатки.",
    }

def _ads_product_diagnosis_status(totals, orders_dynamic=None, ads_traffic_share=None):
    diagnosis = _ads_product_diagnosis(
        totals, orders_dynamic=orders_dynamic, ads_traffic_share=ads_traffic_share
    )
    return diagnosis["status"], diagnosis["conclusion"]


def _aggregate_ads_rows_by_product(summary_stats):
    grouped = {}
    for row in (summary_stats or {}).get("adsRows") or []:
        if not isinstance(row, dict):
            continue
        nm_id = _normalize_ads_nm_id(row.get("nmId") or row.get("nm_id")) or str(id(row))
        if nm_id not in grouped:
            grouped[nm_id] = {"matchedRows": []}
        grouped[nm_id]["matchedRows"].append(row)

    totals_by_product = []
    for rows in grouped.values():
        totals = {"matchedRows": rows["matchedRows"]}
        statuses = [row.get("ads_history_status") for row in rows["matchedRows"] if row.get("ads_history_status")]
        totals["ads_history_status"] = "avg3" if "avg3" in statuses else "previous_day" if "previous_day" in statuses else "insufficient"
        alias_groups = {
            "carts": ("carts", "cartCount", "addToCart", "addToCartCount"),
            "avgPosition": ("avgPosition",),
            "positionDelta": ("positionDelta",),
            "search_bid": ("search_bid",),
            "recommendations_bid": ("recommendations_bid",),
            "search_bid_delta": ("search_bid_delta",),
            "recommendations_bid_delta": ("recommendations_bid_delta",),
        }
        for total_key, keys in alias_groups.items():
            values = []
            previous_values = []
            for row in rows["matchedRows"]:
                value = _first_present_ads_value(row, keys)
                if _is_present(value):
                    values.append(to_number(value))
                for key in keys:
                    previous_value = _ads_previous_value(row, key)
                    if _is_present(previous_value):
                        previous_values.append(to_number(previous_value))
                        break
            if values:
                if total_key in {"avgPosition", "positionDelta", "search_bid", "recommendations_bid", "search_bid_delta", "recommendations_bid_delta"}:
                    totals[total_key] = sum(values) / len(values)
                else:
                    totals[total_key] = sum(values)
            if previous_values:
                previous_key = f"previous_{total_key}"
                if total_key in {"avgPosition", "positionDelta", "search_bid", "recommendations_bid", "search_bid_delta", "recommendations_bid_delta"}:
                    totals[previous_key] = sum(previous_values) / len(previous_values)
                else:
                    totals[previous_key] = sum(previous_values)
        for metric in ("impressions", "clicks", "spend", "orders", "ordersSum"):
            totals[metric] = sum(to_number(row.get(metric)) for row in rows["matchedRows"])
            totals[f"previous_{metric}"] = sum(
                to_number(_ads_previous_value(row, metric)) for row in rows["matchedRows"]
            )
        bid_values = [to_number(row.get("bid")) for row in rows["matchedRows"] if _is_present(row.get("bid"))]
        previous_bid_values = [
            _ads_previous_value(row, "bid")
            for row in rows["matchedRows"]
            if _is_present(_ads_previous_value(row, "bid"))
        ]
        if bid_values:
            totals["bid"] = sum(bid_values) / len(bid_values)
        if previous_bid_values:
            totals["previous_bid"] = sum(previous_bid_values) / len(previous_bid_values)
        totals["ctr"] = totals["clicks"] / totals["impressions"] * 100 if totals["impressions"] else 0
        totals["previous_ctr"] = totals["previous_clicks"] / totals["previous_impressions"] * 100 if totals["previous_impressions"] else 0
        totals["cpc"] = totals["spend"] / totals["clicks"] if totals["clicks"] else 0
        totals["previous_cpc"] = totals["previous_spend"] / totals["previous_clicks"] if totals["previous_clicks"] else 0
        totals["drr"] = totals["spend"] / totals["ordersSum"] * 100 if totals["ordersSum"] else 0
        totals["previous_drr"] = totals["previous_spend"] / totals["previous_ordersSum"] * 100 if totals["previous_ordersSum"] else 0
        totals_by_product.append(totals)
    return totals_by_product


def _ads_bid_delta_rows(summary_stats):
    rows = []
    seen = set()
    for row in (summary_stats or {}).get("adsRows") or []:
        if not isinstance(row, dict):
            continue
        for bid_row in row.get("bidChanges") or []:
            key = (bid_row.get("campaign_id"), bid_row.get("nm_id"))
            if key in seen:
                continue
            seen.add(key)
            rows.append(bid_row)
    return rows


def _ads_bid_change_summary_lines(summary_stats):
    rows = _ads_bid_delta_rows(summary_stats)
    if not rows:
        return ["Изменения ставок:", "данных по ставкам нет"]

    unique_dates_count = None
    analytics = None
    for ads_row in (summary_stats or {}).get("adsRows") or []:
        if not isinstance(ads_row, dict):
            continue
        analytics = analytics or ads_row.get("adsBidAnalytics")
        if ads_row.get("adsBidHistoryUniqueDates") not in (None, ""):
            unique_dates_count = to_number(ads_row.get("adsBidHistoryUniqueDates"))
            break

    if unique_dates_count is not None and unique_dates_count <= 1:
        return ["Изменения ставок:", "История ставок ещё накапливается."]

    if analytics:
        up = to_number(analytics.get("campaigns_raised"))
        down = to_number(analytics.get("campaigns_lowered"))
        same = to_number(analytics.get("campaigns_unchanged"))
    else:
        up = down = same = 0
        for row in rows:
            if not row.get("has_previous_bid_history"):
                continue
            delta = max(
                (
                    to_number(row.get("search_bid_delta"))
                    if _is_present(row.get("search_bid_delta"))
                    else 0
                ),
                (
                    to_number(row.get("recommendations_bid_delta"))
                    if _is_present(row.get("recommendations_bid_delta"))
                    else 0
                ),
                key=abs,
            )
            if delta > 0:
                up += 1
            elif delta < 0:
                down += 1
            else:
                same += 1

    if up + down + same == 0:
        return ["Изменения ставок:", "История ставок ещё накапливается."]

    return [
        "Изменения ставок:",
        "",
        f"* повышены: {_format_number(up)} кампаний",
        f"* снижены: {_format_number(down)} кампаний",
        f"* без изменений: {_format_number(same)} кампаний",
    ]


def _sign_money(value):
    value = to_number(value)
    sign = "+" if value > 0 else ""
    return f"{sign}{_format_number(value)} ₽"


def _product_bid_change(totals):
    changes = []
    for row in totals.get("matchedRows") or []:
        for bid_row in row.get("bidChanges") or []:
            if not bid_row.get("has_previous_bid_history"):
                continue
            for kind, label in (
                ("search", "Поиск"),
                ("recommendations", "Рекомендации"),
            ):
                delta = bid_row.get(f"{kind}_bid_delta")
                previous = bid_row.get(f"previous_{kind}_bid")
                current = bid_row.get(f"{kind}_bid")
                if not (
                    _is_present(delta)
                    and _is_present(previous)
                    and _is_present(current)
                ):
                    continue
                if to_number(delta) == 0:
                    continue
                changes.append(
                    {"label": label, "previous": previous, "current": current, "delta": delta}
                )
    if not changes:
        return None
    main = max(changes, key=lambda item: abs(to_number(item.get("delta"))))
    return {**main, "changes": changes}


def _bid_impact_conclusion(totals, default_conclusion):
    change = _product_bid_change(totals)
    if not change:
        return default_conclusion
    delta = to_number(change.get("delta"))
    impressions_dynamic = _ads_metric_dynamic(totals, "impressions")
    clicks_dynamic = _ads_metric_dynamic(totals, "clicks")
    orders_dynamic = _ads_metric_dynamic(totals, "orders")
    ctr_dynamic = _ads_metric_dynamic(totals, "ctr")
    if delta < 0 and impressions_dynamic is not None and impressions_dynamic < 0:
        return "Снижение ставки могло привести к сокращению рекламного охвата."
    if (
        delta > 0
        and impressions_dynamic is not None
        and impressions_dynamic > 0
        and (
            ctr_dynamic is None
            or ctr_dynamic <= 0
            or clicks_dynamic is None
            or clicks_dynamic <= 0
        )
    ):
        return "Ставка была увеличена, но CTR не улучшился. Проверить главное фото, цену и позицию товара."
    if delta > 0 and impressions_dynamic is not None and impressions_dynamic > 0:
        return "Ставка была увеличена и дала дополнительный рекламный охват."
    if delta == 0 and (
        (impressions_dynamic is not None and impressions_dynamic < 0)
        or (clicks_dynamic is not None and clicks_dynamic < 0)
    ):
        return "Просадка не связана с изменением ставки."
    return default_conclusion

def _ads_diagnosis_lines(ads_summary, summary_stats=None):
    advertised_sku = ads_summary.get("advertisedSku")
    total_sku = ads_summary.get("totalSku")
    product_totals = _aggregate_ads_rows_by_product(summary_stats)
    counters = {"green": 0, "yellow": 0, "red": 0}
    confidence_counters = {"Высокая": 0, "Средняя": 0, "Низкая": 0}
    campaigns_with_history = to_number(
        ads_summary.get("campaignsWithHistory")
        or ads_summary.get("campaigns_with_history")
        or (summary_stats or {}).get("campaignsWithHistory")
        or 0
    )
    low_ads_quality = (
        ads_summary.get("adsCoverageConfidence") == "LOW"
        or (summary_stats or {}).get("adsCoverageConfidence") == "LOW"
        or (summary_stats or {}).get("adsApiHad429")
        or campaigns_with_history == 0
    )
    for totals in product_totals:
        diagnosis = _ads_product_diagnosis(totals)
        confidence_counters[_ads_diagnosis_confidence(totals)] += 1
        status = diagnosis["status"]
        if low_ads_quality and not _ads_has_enough_diagnosis_data(totals):
            status = "yellow"
        counters[status] += 1

    diagnostic = (
        "ADS DIAGNOSIS QUALITY:\n"
        f"high confidence: {confidence_counters['Высокая']}\n"
        f"medium confidence: {confidence_counters['Средняя']}\n"
        f"low confidence: {confidence_counters['Низкая']}"
    )
    logger.info(diagnostic)
    print(diagnostic)

    if not product_totals and advertised_sku:
        counters["yellow"] = int(to_number(advertised_sku))

    if counters["red"] > counters["yellow"] and counters["red"] > counters["green"]:
        conclusion = "Есть подтверждённые рекламные проблемы: высокий объём показов сочетается со слабым CTR, кликами или заказами. Проверить карточки, цену, позиции и релевантность показов."
    elif counters["yellow"] > counters["green"] and counters["yellow"] >= counters["red"]:
        conclusion = "Реклама не выглядит главной причиной просадки. Нужно проверять органику, карточки, цену, конверсию и остатки."
    else:
        conclusion = "По доступным данным рекламная воронка работает стабильно."

    coverage = (
        f"{_format_number(advertised_sku)}/{_format_number(total_sku)}"
        if advertised_sku is not None and total_sku is not None
        else _format_number(len(product_totals))
    )
    return [
        "📊 <b>Рекламный диагноз</b>",
        "",
        f"Товаров с рекламой: {coverage}",
        "",
        f"Средний CTR: {_format_number(ads_summary.get('currentCtr'))}%",
        f"Средний CPC: {_format_number(ads_summary.get('currentCpc'))} ₽",
        f"Средний ДРР: {_format_number(ads_summary.get('currentDrr'))}%",
        "",
        f"🟢 Реклама работает стабильно: {_format_number(counters['green'])}",
        f"🟡 Недостаточно данных / причина не подтверждена: {_format_number(counters['yellow'])}",
        f"🔴 Требуется проверка рекламы: {_format_number(counters['red'])}",
        "",
        *_ads_bid_change_summary_lines(summary_stats),
        "",
        "Вывод по рекламе:",
        conclusion,
    ]


def _ads_summary_lines(ads_summary, summary_stats=None):
    ads_summary = ads_summary or {}
    advertised_sku = ads_summary.get("advertisedSku")
    total_sku = ads_summary.get("totalSku")
    coverage = (
        f"{_format_number(advertised_sku)}/{_format_number(total_sku)} товаров"
        if advertised_sku is not None and total_sku is not None
        else "данные есть"
    )
    source = ads_summary.get("source") or ads_summary.get("adsSource") or "WB Ads API"
    lines = [
        "📢 <b>Реклама:</b> "
        f"{coverage}, CTR {_format_number(ads_summary.get('currentCtr'))}%, "
        f"клик {_format_number(ads_summary.get('currentCpc'))} ₽, "
        f"ДРР {_format_number(ads_summary.get('currentDrr'))}%.",
    ]
    if _ads_rows_count(summary_stats, ads_summary) > 0:
        lines.append(f"Источник: {source}")
    lines.append("")
    lines.extend(_ads_diagnosis_lines(ads_summary, summary_stats))
    if _has_partial_ads_data(summary_stats, ads_summary):
        lines.append("")
        lines.append("⚠️ Данные рекламы частичные.")
    elif ads_summary.get("fallbackUsed"):
        lines.append("")
        lines.append(
            "⚠️ Актуальные данные рекламы не получены от WB. "
            "Используются последние доступные данные из истории."
        )
    return lines


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
    block_lines = _ads_summary_lines(ads_summary, summary_stats)
    block_lines.append(f"проблем рекламы: {_format_number(ads_problem_count)}")
    block_lines.append(f"проблемных кампаний: {_format_number(problem_campaigns)}")
    qbiki_line = _qbiki_unavailable_line(summary_stats)
    if qbiki_line:
        block_lines.append(qbiki_line)

    if not ads_records:
        block_lines.append(
            "Критичных рекламных проблем по доступным данным не найдено."
        )
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
        reason_text = str(
            record.get("problemLabel")
            or record.get("problemType")
            or "реклама стала неэффективной"
        )
        suppressed_metrics = record.get("suppressedAdsMetrics") or []
        if suppressed_metrics:
            reason_text = (
                reason_text
                + ": "
                + ", ".join(str(metric) for metric in suppressed_metrics)
            )
        reason = html.escape(reason_text)
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
    baseline_counts = summary_stats.get("baselineTypeCounts") or {}
    if baseline_mode == "avg_7d":
        return "сравнение со средним за 7 дней"
    if baseline_mode == "avg_3d":
        return "сравнение со средним за 3 дня"
    if baseline_mode in {"fallback_previous_day", "previous_day", "prev_day"}:
        return "сравнение со вчерашним днём"
    if baseline_counts:
        dominant = max(baseline_counts, key=lambda key: baseline_counts.get(key) or 0)
        if dominant == "avg_3d":
            return "сравнение с доступной историей: в основном среднее за 3 дня"
        if dominant == "fallback_previous_day":
            return "сравнение с доступной историей: в основном вчерашний день"
    if baseline_mode:
        return f"сравнение: {html.escape(str(baseline_mode))}"
    return "сравнение с доступной историей"


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

    for problem in problems:
        if not _is_insufficient_history_problem(problem):
            return problem
    return problems[0]


def _executive_problem_title(product):
    title_value = product.get("title") or "Без названия"
    title = html.escape(str(title_value))
    nm_id = _format_wb_nm_id(product.get("nmId"), missing="—")

    return f"{title} — WB {nm_id}"


def _executive_problem_line(index, product, insights_by_key):
    problems = product.get("problems") or []
    by_metric = {str(problem.get("metric") or ""): problem for problem in problems}
    business_lines = [
        _format_metric_transition_bullet(by_metric.get("orderSum"), "Выручка", " ₽"),
        _format_metric_transition_bullet(by_metric.get("orderCount"), "Заказы"),
        _format_metric_transition_bullet(
            by_metric.get("cartToOrderPercent"), "Конверсия в заказ", "%"
        ),
        _format_metric_transition_bullet(by_metric.get("openCount"), "Переходы"),
        _format_metric_transition_bullet(by_metric.get("cartCount"), "Корзина"),
        _format_metric_transition_bullet(
            by_metric.get("addToCartPercent"), "Конверсия в корзину", "%"
        ),
    ]
    ads_problem = next(
        (problem for problem in problems if _problem_zone(problem) == "ADS"), None
    )
    if ads_problem and not any(business_lines):
        problem_type = str(ads_problem.get("problemType") or "")
        label = "CTR рекламы" if "ctr" in problem_type else "реклама"
        business_lines.append(
            f"— {label} снизился на {abs(to_number(ads_problem.get('dynamicPercent'))):.0f}%"
        )
    business_lines = [line for line in business_lines if line]

    if business_lines:
        problem_summary = (
            _product_problem_summary(product) or "есть фактическая просадка"
        )
        action = "проверить остатки, цену и видимость."
        if _problem_by_metric(product, "orderSum") and _problem_by_metric(
            product, "orderCount"
        ):
            action = "проверить рекламный трафик, цену, сроки доставки и остатки."
        root_cause_line = ""
        if _product_ads_decline_matches_funnel(product):
            root_cause_line = "\nПричина вероятно связана с рекламным трафиком"
        return (
            f"{index}. <b>{_executive_problem_title(product)}</b>\n"
            f"Проблема: {html.escape(problem_summary)}\n"
            + "\n".join(business_lines)
            + root_cause_line
            + f"\nЧто делать: {html.escape(action)}"
        )

    primary_problem = _product_primary_problem(product)
    insight = insights_by_key.get(_problem_group_key(product)) or {}
    problem = html.escape(_human_readable_problem_type(primary_problem))
    dynamic = html.escape(
        _format_dynamic_percent(primary_problem.get("dynamicPercent"))
    )
    consequence_text = str(
        insight.get("reason")
        or primary_problem.get("problemLabel")
        or "есть риск потери заказов и выручки"
    )
    action_text = str(
        primary_problem.get("recommendation")
        or ", ".join(str(item) for item in insight.get("whatToCheck") or [])
        or "проверить карточку, цену, рекламу и остатки"
    )
    for technical, user_text in (
        ("INSUFFICIENT_HISTORY", ""),
        ("STOCK_FORECAST", "Риск скорого окончания остатков"),
        ("SKU", "товар"),
        ("[TODAY]", ""),
        ("[NOW]", ""),
        ("NORMAL", "без признаков перегрева"),
        ("n/a", ""),
    ):
        consequence_text = consequence_text.replace(technical, user_text).strip()
        action_text = action_text.replace(technical, user_text).strip()
    consequence = html.escape(consequence_text)
    action = html.escape(action_text)

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
        return f"Проверить {insight.get('rootCauseZone')} по товарам из блока главных проблем"
    return html.escape(
        str(
            problem.get("recommendation")
            or "Проверить ключевой сигнал по товарам из блока главных проблем"
        )
    )


def _product_action_text(product, insights_by_key):
    problems = product.get("problems") or []
    by_metric = {str(problem.get("metric") or ""): problem for problem in problems}
    identity = _format_product_identity(product)

    def transition(metric, label):
        problem = by_metric.get(metric)
        if not problem:
            return ""
        selected = problem.get("selectedValue")
        past = problem.get("pastValue")
        if _is_present(selected) and _is_present(past):
            if to_number(selected) < to_number(past):
                return f"{label} упали с {_format_number(past)} до {_format_number(selected)}"
            return ""
        return f"{label} {_format_dynamic_percent(problem.get('dynamicPercent'))}"

    facts = [
        item
        for item in (
            transition("openCount", "переходы в карточку"),
            transition("orderCount", "заказы"),
            transition("orderSum", "выручка"),
            transition("cartCount", "корзины"),
        )
        if item
    ]
    if facts:
        return f"Проверить {identity}: " + ", ".join(facts[:2]) + "."

    primary = _product_primary_problem(product)
    return _business_action_text(
        primary, insights_by_key.get(_problem_group_key(primary))
    )


def _build_executive_actions_block(
    priority_records, root_cause_insights, forecast_records=None
):
    actionable = [
        record
        for record in priority_records
        if not record.get("isSuppressed")
        and not _is_insufficient_history_problem(record)
    ]
    if not actionable:
        return ""

    insights_by_key = _build_insights_by_key(root_cause_insights)
    products = _group_problems_by_product(actionable)
    lines = []
    seen = set()
    for product in products:
        action = _product_action_text(product, insights_by_key)
        if action in seen:
            continue
        seen.add(action)
        lines.append(f"{len(lines) + 1}. {action}")
        if len(lines) == EXECUTIVE_ACTIONS_LIMIT:
            break

    if any(
        _is_predictive_problem(record)
        for record in _problems_to_records(forecast_records)
    ):
        forecast_action = "Проверить остатки по товарам с риском OOS в прогнозе."
        if forecast_action not in seen and len(lines) < EXECUTIVE_ACTIONS_LIMIT:
            lines.append(f"{len(lines) + 1}. {forecast_action}")

    return "✅ <b>Что делать сегодня</b>\n" + "\n".join(lines)


def _build_executive_top_problems(
    problem_products, root_cause_insights, exclude_keys=None
):
    if not problem_products:
        return ""

    exclude_keys = exclude_keys or set()
    insights_by_key = _build_insights_by_key(root_cause_insights)
    top_products = [
        product
        for product in problem_products
        if _problem_group_key(product) not in exclude_keys
        if _has_executive_problem_text(_product_primary_problem(product))
    ][:EXECUTIVE_PROBLEMS_LIMIT]
    if not top_products:
        return ""
    lines = [
        _executive_problem_line(index, product, insights_by_key)
        for index, product in enumerate(top_products, start=1)
    ]

    return "🔴 <b>Главные проблемы</b>\n" + "\n\n".join(lines)


def _problem_by_metric(product, metric):
    for problem in product.get("problems") or []:
        if str(problem.get("metric") or "") == metric and _is_actual_drop(problem):
            return problem
    return None


def _format_metric_drop_line(problem, label, suffix=""):
    if not problem:
        return ""
    past = problem.get("pastValue")
    selected = problem.get("selectedValue")
    dynamic = problem.get("dynamicPercent")
    if not (_is_present(past) and _is_present(selected)):
        return ""
    return (
        f"{label} {_format_number(past)}{suffix} → "
        f"{_format_number(selected)}{suffix} ({_format_dynamic_value(dynamic)})"
    )


def _format_metric_bullet(problem, label):
    if not problem:
        return ""
    return f"— {label} {_format_dynamic_value(problem.get('dynamicPercent'))}"


def _format_metric_transition_bullet(problem, label, suffix=""):
    if not problem:
        return ""
    past = problem.get("pastValue")
    selected = problem.get("selectedValue")
    if not (_is_present(past) and _is_present(selected)):
        return _format_metric_bullet(problem, label)
    return (
        f"— {label}: {_format_number(past)}{suffix} → "
        f"{_format_number(selected)}{suffix} "
        f"({_format_dynamic_value(problem.get('dynamicPercent'))})"
    )


def _problem_has_ads_data(problem):
    if _problem_zone(problem) == "ADS":
        return True
    return any(
        _is_present(problem.get(key))
        for key in (
            "ctr",
            "cpc",
            "drr",
            "previousCtr",
            "previousCpc",
            "previousDrr",
            "currentCtr",
            "currentCpc",
            "currentDrr",
        )
    )


def _product_ads_problem(product):
    return next(
        _product_ads_problems(product),
        None,
    )


def _product_ads_problems(product):
    return [
        problem
        for problem in product.get("problems") or []
        if _problem_zone(problem) == "ADS" or _problem_has_ads_data(problem)
    ]


def _metric_decreased(problem, previous_key, current_key, fallback_key=None):
    previous = problem.get(previous_key)
    current = problem.get(current_key)
    if _is_present(previous) and _is_present(current):
        return to_number(current) < to_number(previous)
    if fallback_key and _is_present(problem.get(fallback_key)):
        return to_number(problem.get(fallback_key)) < 0
    return False


def _metric_increased(problem, previous_key, current_key, fallback_key=None):
    previous = problem.get(previous_key)
    current = problem.get(current_key)
    if _is_present(previous) and _is_present(current):
        return to_number(current) > to_number(previous)
    if fallback_key and _is_present(problem.get(fallback_key)):
        return to_number(problem.get(fallback_key)) > 0
    return False


def _product_ads_decline_matches_funnel(product):
    ads_problems = _product_ads_problems(product)
    if not ads_problems:
        return False

    ctr_down = any(
        _metric_decreased(problem, "previousCtr", "ctr", "dynamicPercent")
        for problem in ads_problems
    )
    cpc_up = any(
        _metric_increased(problem, "previousCpc", "cpc", "cpcDynamic")
        for problem in ads_problems
    )
    funnel_down = any(
        _problem_by_metric(product, metric)
        for metric in ("openCount", "orderCount", "orderSum")
    )
    return ctr_down and cpc_up and funnel_down


def _product_problem_summary(product):
    has_revenue = bool(_problem_by_metric(product, "orderSum"))
    has_orders = bool(_problem_by_metric(product, "orderCount"))
    has_conversion = bool(_problem_by_metric(product, "cartToOrderPercent"))
    has_traffic = bool(_problem_by_metric(product, "openCount"))
    has_cart = bool(
        _problem_by_metric(product, "cartCount")
        or _problem_by_metric(product, "addToCartPercent")
    )

    if has_revenue and has_orders:
        return "просели заказы и выручка"
    if has_revenue:
        return "просела выручка"
    if has_orders:
        return "резкое падение заказов"
    if has_conversion:
        return "просела конверсия в заказ"
    if has_traffic:
        return "просели переходы"
    if has_cart:
        return "просела корзина"
    return ""


def _main_business_decline_product(problem_products):
    candidates = [
        product
        for product in problem_products or []
        if to_number(product.get("businessImpactScore")) > 0
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda product: business_ranking_key(
            _product_primary_problem(product), True
        ),
    )[0]


def _build_executive_insight(problem_products, root_cause_insights, summary_stats):
    main_decline_product = _main_business_decline_product(problem_products)
    if main_decline_product:
        title = _format_product_identity(main_decline_product)
        lines = [
            _format_metric_drop_line(
                _problem_by_metric(main_decline_product, "orderCount"), "заказы"
            ),
            _format_metric_drop_line(
                _problem_by_metric(main_decline_product, "orderSum"), "выручка", " ₽"
            ),
            _format_metric_drop_line(
                _problem_by_metric(main_decline_product, "cartToOrderPercent"),
                "конверсия в заказ",
                "%",
            ),
            _format_metric_drop_line(
                _problem_by_metric(main_decline_product, "openCount"), "переходы"
            ),
            _format_metric_drop_line(
                _problem_by_metric(main_decline_product, "cartCount"),
                "добавления в корзину",
            ),
        ]
        lines = [line for line in lines if line]
        if lines:
            insight_text = (
                "🧠 <b>Главный инсайт:</b> Главная просадка дня — "
                f"{title}:\n" + ";\n".join(lines) + "."
            )
            ads_problems = _product_ads_problems(main_decline_product)
            if ads_problems:
                ads_parts = []
                if any(
                    _metric_decreased(problem, "previousCtr", "ctr", "dynamicPercent")
                    for problem in ads_problems
                ):
                    ads_parts.append("CTR снизился")
                if any(
                    _metric_increased(problem, "previousCpc", "cpc", "cpcDynamic")
                    for problem in ads_problems
                ):
                    ads_parts.append("стоимость клика выросла")
                if any(
                    _metric_increased(problem, "previousDrr", "drr", "drrDynamic")
                    for problem in ads_problems
                ):
                    ads_parts.append("ДРР вырос")
                if ads_parts:
                    insight_text += (
                        "\nНа фоне этого реклама по товару ухудшилась: "
                        + ", ".join(ads_parts)
                        + "."
                    )
            return insight_text

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
            "из-за отсутствия остатков по ключевым товарам. Остатки WB: "
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

        if (
            insight
            and insight.get("reason")
            and not _is_insufficient_history_problem(primary_problem)
            and insight.get("rootCauseZone") != "INSUFFICIENT_HISTORY"
            and insight.get("reason") != "INSUFFICIENT_HISTORY"
        ):
            zone = html.escape(str(insight.get("rootCauseZone") or "причина"))
            reason = html.escape(str(insight.get("reason")))
            return f"🧠 <b>Главный инсайт:</b> {zone}: {reason}"

    if (summary_stats or {}).get("orderCountDynamic", 0) < 0:
        return "🧠 <b>Главный инсайт:</b> просадка заказов требует проверки трафика, конверсии и наличия."

    return "🧠 <b>Главный инсайт:</b> критичный управленческий сигнал не выявлен."


def _product_metric_problem(product, metric):
    for problem in product.get("problems") or []:
        if str(problem.get("metric") or "") == metric:
            return problem
    return None


def _product_metric_current_value(product, metric):
    problem = _product_metric_problem(product, metric)
    if not problem:
        return None
    selected = problem.get("selectedValue")
    if _is_present(selected):
        return to_number(selected)
    return None


def _product_metric_dynamic(product, metric):
    problem = _product_metric_problem(product, metric)
    if not problem:
        return None
    dynamic = problem.get("dynamicPercent")
    if _is_present(dynamic):
        return to_number(dynamic)
    selected = problem.get("selectedValue")
    past = problem.get("pastValue")
    if _is_present(selected) and _is_present(past) and to_number(past):
        return (to_number(selected) - to_number(past)) / to_number(past) * 100
    return None


def _format_optional_dynamic(value):
    if value is None:
        return "н/д"
    return _format_dynamic_value(value)


def _merge_products_by_nm_id(problem_products):
    grouped = {}
    for index, product in enumerate(problem_products or []):
        group_key = _problem_group_key(product)
        if group_key not in grouped:
            grouped[group_key] = {
                **product,
                "first_index": product.get("first_index", index),
                "problems": [],
            }
        grouped[group_key]["problems"].extend(product.get("problems") or [])

    for product in grouped.values():
        has_positive_funnel_problem = any(
            _is_funnel_problem(problem) and _business_impact_score(problem) > 0
            for problem in product.get("problems") or []
        )
        product["problems"].sort(
            key=lambda problem: _business_sort_key(problem, has_positive_funnel_problem)
        )
        product["businessImpactScore"] = max(
            (_business_impact_score(problem) for problem in product["problems"]),
            default=to_number(product.get("businessImpactScore")),
        )
    return list(grouped.values())


def _top_drop_metric_dynamics(product):
    dynamics = []
    for metric, label in TOP_DROP_METRICS:
        dynamic = _product_metric_dynamic(product, metric)
        if dynamic is not None and dynamic < 0:
            dynamics.append((metric, label, dynamic))
    return dynamics


def _top_drop_sort_key(product):
    primary_problem = _product_primary_problem(product)
    return (
        _business_sort_key(primary_problem),
        -_product_lost_revenue(product),
        -_product_lost_orders(product),
        product.get("first_index", 0),
    )


def _log_telegram_top_drops(raw_products, selected_products):
    raw_problems = [
        problem
        for product in raw_products or []
        for problem in product.get("problems") or []
    ]
    unique_nm_ids = []
    for product in raw_products or []:
        nm_id = str(product.get("nmId") or "")
        if nm_id and nm_id != "n/a":
            unique_nm_ids.append(nm_id)
    selected_nm_ids = [
        str(product.get("nmId") or "") for product, *_ in selected_products
    ]
    selected_titles = [
        str(product.get("title") or "Без названия") for product, *_ in selected_products
    ]
    diagnostic = (
        "TELEGRAM TOP DROPS SOURCE: FUNNEL_PROBLEMS\n"
        "TELEGRAM TOP DROPS:\n"
        f"raw problems: {len(raw_problems)}\n"
        f"unique nmIds: {unique_nm_ids}\n"
        f"selected nmIds: {selected_nm_ids}\n"
        f"selected titles: {selected_titles}"
    )
    logger.info(diagnostic)
    print(diagnostic)


def _log_telegram_top_drops_grouping(stage, problems_or_products):
    if stage == "before":
        nm_ids = [
            str(problem.get("nmId") or "n/a") for problem in problems_or_products or []
        ]
        diagnostic = (
            "TELEGRAM TOP DROPS GROUPING BEFORE:\n"
            f"raw problems: {len(problems_or_products or [])}\n"
            f"raw nmIds: {nm_ids}"
        )
    else:
        nm_ids = [
            str(product.get("nmId") or "n/a") for product in problems_or_products or []
        ]
        titles = [
            str(product.get("title") or "Без названия")
            for product in problems_or_products or []
        ]
        diagnostic = (
            "TELEGRAM TOP DROPS GROUPING AFTER:\n"
            f"unique nmIds: {nm_ids}\n"
            f"unique titles: {titles}"
        )
    logger.info(diagnostic)
    print(diagnostic)


def _group_funnel_top_drop_products(records):
    funnel_problems = [
        record
        for record in records or []
        if _is_funnel_problem(record)
        and str(record.get("metric") or "")
        in {metric for metric, _label in TOP_DROP_METRICS}
    ]
    _log_telegram_top_drops_grouping("before", funnel_problems)

    grouped_products = {}
    for index, problem in enumerate(funnel_problems):
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

    products = sorted(
        grouped_products.values(),
        key=lambda product: (
            -_product_lost_revenue(product),
            -_product_lost_orders(product),
            product.get("first_index", 0),
        ),
    )
    _log_telegram_top_drops_grouping("after", products)
    return products


def _build_product_movement_block(problem_products, direction, summary_stats=None):
    products = []
    source_products = problem_products or []
    for product in source_products:
        revenue_dynamic = _product_metric_dynamic(product, "orderSum")
        orders_dynamic = _product_metric_dynamic(product, "orderCount")
        traffic_dynamic = _product_metric_dynamic(product, "openCount")
        open_count = _product_metric_current_value(product, "openCount")
        if direction == "drop":
            metric_dynamics = _top_drop_metric_dynamics(product)
            if not (
                metric_dynamics
                or _product_lost_revenue(product) > 0
                or _product_lost_orders(product) > 0
            ):
                continue
            sort_key = _top_drop_sort_key(product)
        else:
            metric_dynamics = []
            if not (
                (revenue_dynamic is not None and revenue_dynamic > 0)
                and (orders_dynamic is not None and orders_dynamic > 0)
            ):
                continue
            sort_key = (revenue_dynamic or 0, orders_dynamic or 0)

        products.append(
            (
                product,
                sort_key,
                orders_dynamic,
                revenue_dynamic,
                traffic_dynamic,
                open_count,
                metric_dynamics,
            )
        )

    title = (
        "🔴 <b>ТОП-3 просадки</b>" if direction == "drop" else "🟢 <b>ТОП-3 роста</b>"
    )
    if not products:
        if direction == "growth":
            return ""
        return title + "\nПросадок заказов/выручки не найдено."

    products = sorted(products, key=lambda item: item[1], reverse=direction != "drop")[
        :3
    ]
    if direction == "drop":
        _log_telegram_top_drops(source_products, products)
        _log_telegram_ads_product_breakdown(products, summary_stats)
        logger.debug(
            "TOP DECLINES DEBUG: %s",
            [
                {
                    "nmId": product.get("nmId"),
                    "title": product.get("title"),
                    "businessImpactScore": product.get("businessImpactScore"),
                    "rankingKey": sort_key,
                    "lostRevenue": _product_lost_revenue(product),
                    "lostOrders": _product_lost_orders(product),
                }
                for product, sort_key, *_ in products
            ],
        )
    lines = []
    for index, (
        product,
        _sort_key,
        orders_dynamic,
        revenue_dynamic,
        traffic_dynamic,
        open_count,
        metric_dynamics,
    ) in enumerate(products, start=1):
        product_lines = [f"{index}. <b>{_executive_problem_title(product)}</b>"]
        if direction == "drop":
            if metric_dynamics:
                product_lines.extend(
                    f"   {label}: {_format_optional_dynamic(dynamic)}"
                    for _metric, label, dynamic in metric_dynamics
                )
            else:
                product_lines.extend(
                    [
                        f"   Заказы: {_format_optional_dynamic(orders_dynamic)}",
                        f"   Выручка: {_format_optional_dynamic(revenue_dynamic)}",
                        f"   Переходы: {_format_optional_dynamic(traffic_dynamic)}",
                    ]
                )
            ads_breakdown = _build_product_ads_breakdown(
                product, traffic_dynamic, orders_dynamic, summary_stats, open_count
            )
            if ads_breakdown:
                product_lines.append(ads_breakdown)
        lines.append("\n".join(product_lines))
    return title + "\n\n" + "\n\n".join(lines)


def _log_telegram_ads_product_breakdown(products, summary_stats):
    checked = len(products or [])
    with_ads = 0
    without_ads = 0
    for product, *_ in products or []:
        if _product_ads_totals(product, summary_stats) is None:
            without_ads += 1
        else:
            with_ads += 1
    diagnostic = (
        "TELEGRAM ADS PRODUCT BREAKDOWN:\n"
        f"top drop products checked: {checked}\n"
        f"with ads data: {with_ads}\n"
        f"without ads data: {without_ads}"
    )
    logger.info(diagnostic)
    print(diagnostic)


def _normalize_ads_nm_id(value):
    if value in (None, "", "n/a"):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value).strip()
    if number.is_integer():
        return str(int(number))
    return str(value).strip()


def _ads_previous_value(row, metric):
    candidates = [
        f"previous{metric[0].upper()}{metric[1:]}",
        f"previous_{metric}",
        f"past{metric[0].upper()}{metric[1:]}",
    ]
    for key in candidates:
        value = row.get(key)
        if _is_present(value):
            return to_number(value)
    return None


def _product_ads_totals(product, summary_stats):
    nm_id = _normalize_ads_nm_id(product.get("nmId"))
    if not nm_id:
        return None

    matched_rows = [
        row
        for row in (summary_stats or {}).get("adsRows") or []
        if isinstance(row, dict)
        and _normalize_ads_nm_id(row.get("nmId") or row.get("nm_id")) == nm_id
    ]
    if not matched_rows:
        return None

    totals = {"rows": len(matched_rows), "matchedRows": matched_rows}
    statuses = [row.get("ads_history_status") for row in matched_rows if row.get("ads_history_status")]
    totals["ads_history_status"] = "avg3" if "avg3" in statuses else "previous_day" if "previous_day" in statuses else "insufficient"
    alias_groups = {
        "carts": ("carts", "cartCount", "addToCart", "addToCartCount"),
        "avgPosition": ("avgPosition",),
        "positionDelta": ("positionDelta",),
        "search_bid": ("search_bid",),
        "recommendations_bid": ("recommendations_bid",),
        "search_bid_delta": ("search_bid_delta",),
        "recommendations_bid_delta": ("recommendations_bid_delta",),
    }
    for total_key, keys in alias_groups.items():
        values = []
        previous_values = []
        for row in matched_rows:
            value = _first_present_ads_value(row, keys)
            if _is_present(value):
                values.append(to_number(value))
            for key in keys:
                previous_value = _ads_previous_value(row, key)
                if _is_present(previous_value):
                    previous_values.append(to_number(previous_value))
                    break
        if values:
            if total_key in {"avgPosition", "positionDelta", "search_bid", "recommendations_bid", "search_bid_delta", "recommendations_bid_delta"}:
                totals[total_key] = sum(values) / len(values)
            else:
                totals[total_key] = sum(values)
        if previous_values:
            previous_key = f"previous_{total_key}"
            if total_key in {"avgPosition", "positionDelta", "search_bid", "recommendations_bid", "search_bid_delta", "recommendations_bid_delta"}:
                totals[previous_key] = sum(previous_values) / len(previous_values)
            else:
                totals[previous_key] = sum(previous_values)
    campaign_ids = []
    campaign_types = []
    for row in matched_rows:
        campaign_id = (
            row.get("campaignId") or row.get("advertId") or row.get("campaign_id")
        )
        if campaign_id not in (None, "") and str(campaign_id) not in campaign_ids:
            campaign_ids.append(str(campaign_id))
        for campaign_type in _resolve_ads_row_campaign_types(row):
            if campaign_type not in campaign_types:
                campaign_types.append(campaign_type)
    totals["campaignIds"] = campaign_ids
    totals["campaignTypes"] = campaign_types
    for metric in ("impressions", "clicks", "spend", "orders", "ordersSum", "bid"):
        current_values = [to_number(row.get(metric)) for row in matched_rows]
        previous_values = [to_number(_ads_previous_value(row, metric)) for row in matched_rows]
        if metric == "bid":
            weighted_current = [
                (
                    row.get(metric),
                    to_number(row.get("clicks")) or to_number(row.get("impressions")),
                )
                for row in matched_rows
            ]
            weighted_previous = [
                (
                    _ads_previous_value(row, metric),
                    _ads_previous_value(row, "clicks")
                    or _ads_previous_value(row, "impressions"),
                )
                for row in matched_rows
            ]
            current_weight = sum(weight for _, weight in weighted_current)
            previous_weight = sum(weight for _, weight in weighted_previous)
            totals[metric] = (
                sum(to_number(value) * weight for value, weight in weighted_current)
                / current_weight
                if current_weight
                else sum(current_values) / len(current_values)
            )
            totals[f"previous_{metric}"] = (
                sum(to_number(value) * weight for value, weight in weighted_previous)
                / previous_weight
                if previous_weight
                else sum(previous_values) / len(previous_values)
            )
        else:
            totals[metric] = sum(current_values)
            totals[f"previous_{metric}"] = sum(previous_values)

    totals["ctr"] = (
        totals["clicks"] / totals["impressions"] * 100 if totals["impressions"] else 0
    )
    totals["previous_ctr"] = (
        totals["previous_clicks"] / totals["previous_impressions"] * 100
        if totals["previous_impressions"]
        else 0
    )
    totals["cpc"] = totals["spend"] / totals["clicks"] if totals["clicks"] else 0
    totals["previous_cpc"] = (
        totals["previous_spend"] / totals["previous_clicks"]
        if totals["previous_clicks"]
        else 0
    )
    totals["drr"] = (
        totals["spend"] / totals["ordersSum"] * 100 if totals["ordersSum"] else 0
    )
    totals["previous_drr"] = (
        totals["previous_spend"] / totals["previous_ordersSum"] * 100
        if totals["previous_ordersSum"]
        else 0
    )
    return totals



def _parse_ads_row_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.split("T", 1)[0].split(" ", 1)[0]
    for separator in (" — ", " - ", "/"):
        if separator in text:
            text = text.split(separator, 1)[0].strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return None


def _ads_fallback_supabase_used(summary_stats):
    summary_stats = summary_stats or {}
    ads_summary = summary_stats.get("adsSummary") or {}
    source = str(
        ads_summary.get("source")
        or ads_summary.get("adsSource")
        or summary_stats.get("adsSource")
        or ""
    ).lower()
    return bool(
        ads_summary.get("fallbackUsed")
        or summary_stats.get("adsFallbackUsed")
        or "supabase" in source
    )


def _ads_row_data_date(row):
    for key in ("date", "report_date", "reportDate", "selectedPeriod"):
        parsed = _parse_ads_row_date((row or {}).get(key))
        if parsed is not None:
            return parsed
    return None


def _ads_fallback_data_is_stale(totals, summary_stats):
    if not _ads_fallback_supabase_used(summary_stats):
        return False
    row_dates = [
        _ads_row_data_date(row)
        for row in totals.get("matchedRows") or []
        if isinstance(row, dict)
    ]
    row_dates = [value for value in row_dates if value is not None]
    if not row_dates:
        ads_summary = (summary_stats or {}).get("adsSummary") or {}
        row_dates = [
            _parse_ads_row_date(ads_summary.get("selectedPeriod")),
            _parse_ads_row_date(ads_summary.get("date")),
        ]
        row_dates = [value for value in row_dates if value is not None]
    return bool(row_dates) and max(row_dates) < date.today()

def _ads_metric_dynamic(totals, metric):
    previous = totals.get(f"previous_{metric}")
    current = totals.get(metric)
    if previous in (None, "") or to_number(previous) == 0:
        return None
    return (to_number(current) - to_number(previous)) / to_number(previous) * 100


def _format_ads_metric_pair(totals, metric, suffix=""):
    previous = totals.get(f"previous_{metric}")
    current = totals.get(metric)
    return f"{_format_number(previous)}{suffix} → {_format_number(current)}{suffix}"


def _ads_campaign_type_label(value):
    return _ads_campaign_type_label_or_empty(value)


def _ads_campaign_type_label_or_empty(value):
    text = str(value or "").strip()
    numeric_text = text
    try:
        numeric_value = float(text)
        if numeric_value.is_integer():
            numeric_text = str(int(numeric_value))
    except (TypeError, ValueError):
        pass

    normalized_text = numeric_text.lower()
    normalized_text = normalized_text.replace("_", " ").replace("-", " ")
    normalized_text = re.sub(r"\s+", " ", normalized_text).strip()

    numeric_mapping = {
        "4": "CPC, поиск и каталог",
        "5": "Аукцион / поиск",
        "6": "Поиск",
        "7": "Каталог",
        "8": "Автоматическая кампания",
        "9": "Аукцион / поиск",
    }
    if normalized_text in numeric_mapping:
        return numeric_mapping[normalized_text]

    token_mapping = (
        (("manual",), "Ручная кампания"),
        (("unified",), "Автоматическая кампания"),
        (("cpc", "click", "clicks", "per click"), "CPC, поиск и каталог"),
        (
            ("cpm", "view", "views", "impression", "impressions"),
            "CPM, поиск / каталог / рекомендации",
        ),
        (("auto", "automatic", "автомат", "авто"), "Автоматическая кампания"),
        (("auction", "аукцион"), "Аукцион / поиск"),
        (("search", "поиск"), "Поиск"),
        (("catalog", "каталог"), "Каталог"),
        (("recommendation", "рекомендации", "полка"), "Рекомендательные полки"),
    )
    for tokens, label in token_mapping:
        if any(token in normalized_text for token in tokens):
            return label

    return ""


def _ads_raw_json(row):
    raw_json = (row or {}).get("raw_json")
    if not raw_json:
        return {}
    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except (TypeError, ValueError):
            return {}
    if not isinstance(raw_json, dict):
        return {}
    return raw_json


def _ads_raw_json_values(row, key):
    return _ads_list_values(_ads_raw_json(row).get(key))


def _ads_row_campaign_name(row):
    for key in ("campaign_name", "campaignName", "name", "title"):
        value = (row or {}).get(key)
        if value not in (None, ""):
            return str(value)
    for key in ("campaign_name", "campaignName", "name", "title"):
        values = _ads_raw_json_values(row, key)
        if values:
            return values[0]
    return ""


def _ads_campaign_type_from_name(campaign_name):
    return _ads_campaign_type_label_or_empty(campaign_name)


def _resolve_ads_row_campaign_types(row):
    type_keys = (
        "campaign_type",
        "type",
        "campaignType",
        "advert_type",
        "advertType",
        "paymentType",
        "model",
        "placement",
        "bid_type",
        "bidType",
    )
    payment_keys = ("paymentType", "model")
    raw_types = []
    payment_types = []
    for key in type_keys:
        values = _ads_list_values((row or {}).get(key))
        raw_types.extend(values)
        if key in payment_keys:
            payment_types.extend(values)
    for key in type_keys:
        values = _ads_raw_json_values(row, key)
        raw_types.extend(values)
        if key in payment_keys:
            payment_types.extend(values)

    campaign_name = _ads_row_campaign_name(row)
    raw_json_text = json.dumps(_ads_raw_json(row), ensure_ascii=False)
    values_to_resolve = payment_types + raw_types + [campaign_name, raw_json_text]

    resolved_types = []
    for raw_type in values_to_resolve:
        label = _ads_campaign_type_label_or_empty(raw_type)
        if label:
            resolved_types.append(label)
            break

    campaign_ids = _ads_row_campaign_ids(row)
    logger.info(
        "TELEGRAM ADS CAMPAIGN TYPE:\n"
        "campaign_id: %s\n"
        "raw_type: %s\n"
        "payment_type: %s\n"
        "campaign_name: %s\n"
        "resolved_type: %s",
        ", ".join(campaign_ids),
        ", ".join(raw_types),
        ", ".join(payment_types),
        campaign_name,
        ", ".join(resolved_types),
    )
    return resolved_types


def _format_ads_campaign_meta(totals):
    campaign_ids = [
        str(value)
        for value in totals.get("campaignIds") or []
        if value not in (None, "")
    ]
    campaign_types = [
        label
        for label in (
            _ads_campaign_type_label(value)
            for value in totals.get("campaignTypes") or []
            if value not in (None, "")
        )
        if label
    ]
    if not campaign_ids and not campaign_types:
        return []
    lines = []
    if len(campaign_ids) > 1:
        lines.append(f"   ID кампаний: {html.escape(', '.join(campaign_ids))}")
    elif campaign_ids:
        lines.append(f"   ID кампании: {html.escape(campaign_ids[0])}")
    unique_types = list(dict.fromkeys(campaign_types))
    if len(unique_types) > 1:
        lines.append(f"   Типы: {html.escape(', '.join(unique_types))}")
    elif unique_types:
        lines.append(f"   Тип кампании: {html.escape(unique_types[0])}")
    return lines


def _product_ads_open_count(product, fallback_open_count=None):
    if _is_present(fallback_open_count):
        return to_number(fallback_open_count)
    for key in ("openCount", "selectedOpenCount", "currentOpenCount", "open_count"):
        if _is_present(product.get(key)):
            return to_number(product.get(key))
    return _product_metric_current_value(product, "openCount")


def _product_ads_traffic_share(product, totals, open_count=None):
    open_count = _product_ads_open_count(product, open_count)
    if open_count is None or open_count <= 0:
        return None
    clicks = to_number(totals.get("clicks"))
    if clicks >= 0:
        return round(clicks / open_count * 100, 2)
    return None


def _is_high_ads_cpc(value):
    return to_number(value) >= 1000


def _product_ads_conclusion(
    totals,
    traffic_dynamic,
    orders_dynamic,
    ads_traffic_share=None,
    ads_api_partial=False,
):
    _, conclusion = _ads_product_diagnosis_status(
        totals, orders_dynamic=orders_dynamic, ads_traffic_share=ads_traffic_share
    )
    return conclusion


def _format_optional_ads_line(label, value, suffix=""):
    if not _is_present(value):
        return None
    return f"   {label}: {_format_number(value)}{suffix}"


def _format_product_ads_funnel_lines(totals):
    if not totals:
        return []
    rows = []
    for label, key, suffix in (
        ("Показы", "impressions", ""),
        ("Клики", "clicks", ""),
        ("CTR", "ctr", "%"),
        ("CPC", "cpc", " ₽"),
        ("Добавили в корзину", "carts", ""),
        ("Заказы", "orders", ""),
        ("ДРР", "drr", "%"),
        ("Средняя позиция", "avgPosition", ""),
    ):
        value = totals.get(key)
        if label == "Средняя позиция" and (not _is_present(value) or to_number(value) <= 0):
            continue
        if _is_present(value):
            rows.append(f"   {label}: {_format_number(value)}{suffix}")
    clicks = to_number(totals.get("clicks"))
    if clicks > 0 and _is_present(totals.get("orders")):
        rows.append(
            f"   CR клики → заказ: {_format_number(to_number(totals.get('orders')) / clicks * 100)}%"
        )
    carts = _ads_cart_value(totals)
    if clicks > 0 and carts is not None:
        rows.append(f"   CR клики → корзина: {_format_number(carts / clicks * 100)}%")
    if carts is not None and carts > 0 and _is_present(totals.get("orders")):
        rows.append(
            f"   CR корзина → заказ: {_format_number(to_number(totals.get('orders')) / carts * 100)}%"
        )
    return ["", "   Воронка рекламы:", *rows] if rows else []


def _format_product_ads_bid_change_lines(totals):
    if not totals:
        return []
    lines = []
    for kind, label in (("search", "Поиск"), ("recommendations", "Рекомендации")):
        previous = totals.get(f"previous_{kind}_bid")
        current = totals.get(f"{kind}_bid")
        delta = totals.get(f"{kind}_bid_delta")
        if not (_is_present(previous) and _is_present(current)):
            continue
        if _is_present(delta) and to_number(delta) == 0:
            continue
        if not _is_present(delta) and to_number(previous) == to_number(current):
            continue
        lines.append(
            f"   {label}: было {_format_number(previous)} ₽ → стало {_format_number(current)} ₽"
        )
    if lines:
        return ["", "   Изменение ставки:", *lines]
    bid_change = _product_bid_change(totals)
    if not bid_change:
        return []
    for change in bid_change.get("changes") or [bid_change]:
        lines.append(
            f"   {change['label']}: было {_format_number(change['previous'])} ₽ → стало {_format_number(change['current'])} ₽"
        )
    return ["", "   Изменение ставки:", *lines] if lines else []


def _format_product_ads_diagnosis_block(diagnosis):
    totals = diagnosis.get("totals") or {}
    confidence = diagnosis.get("confidence") or _ads_diagnosis_confidence(totals)
    source = diagnosis.get("source") or _ads_problem_source(totals, diagnosis.get("status"))
    lines = [
        "   📢 <b>Рекламный диагноз</b>",
        "",
        "   Источник проблемы:",
        f"   {source}",
        "",
        "   Надёжность диагноза:",
        f"   {confidence}",
        "",
        "   Сравнение рекламы:",
        f"   {_ads_comparison_label(totals)}",
        "",
        "   Причина просадки:",
        f"   {diagnosis['reason']}",
        "",
        "   Подтверждение:",
    ]
    lines.extend(f"   {line}" for line in diagnosis.get("confirmation") or [])
    lines.extend(_format_product_ads_funnel_lines(totals))
    if totals.get("adsBidHistoryReady"):
        lines.extend(_format_product_ads_bid_change_lines(totals))
    else:
        lines.extend(["", "   Изменение ставки:", "   История ставок ещё накапливается."])
    lines.extend(
        [
            "",
            "   Вывод:",
            f"   {diagnosis['conclusion']}",
        ]
    )
    return "\n".join(lines)

def _build_product_ads_breakdown(
    product, traffic_dynamic, orders_dynamic, summary_stats, open_count=None
):
    no_data_block = _format_product_ads_diagnosis_block(
        _ads_product_diagnosis(None, orders_dynamic=orders_dynamic)
    )
    if _ads_rows_count(summary_stats) == 0:
        return no_data_block

    totals = _product_ads_totals(product, summary_stats)
    if totals is None:
        return no_data_block

    current_open_count = _product_ads_open_count(product, open_count)
    ads_clicks = totals.get("clicks")
    if current_open_count is not None and float(current_open_count) > 0:
        ads_traffic_share = round(
            float(ads_clicks or 0) / float(current_open_count) * 100, 2
        )
    else:
        ads_traffic_share = totals.get("adsTrafficShare")
    logger.info(
        "TELEGRAM ADS PRODUCT DATA:\nnmId: %s\nadsClicks: %s\ncurrentOpenCount: %s\nadsTrafficShare: %s",
        product.get("nmId"),
        ads_clicks,
        current_open_count if current_open_count is not None else "",
        ads_traffic_share if ads_traffic_share is not None else "",
    )

    if _ads_fallback_data_is_stale(totals, summary_stats):
        return _format_product_ads_diagnosis_block(
            _ads_product_diagnosis(None, orders_dynamic=orders_dynamic)
        )

    ads_bid_history_ready = _ads_bid_history_ready(summary_stats)
    totals["adsBidHistoryReady"] = ads_bid_history_ready
    diagnosis = _ads_product_diagnosis(
        totals,
        orders_dynamic=orders_dynamic,
        ads_traffic_share=ads_traffic_share,
        open_count=current_open_count,
        use_bid_history=ads_bid_history_ready,
    )
    diagnosis = dict(diagnosis)
    diagnosis["totals"] = totals
    diagnosis.setdefault("confidence", _ads_diagnosis_confidence(totals))
    diagnosis.setdefault("source", _ads_problem_source(totals, diagnosis.get("status"), ads_traffic_share))
    bid_change = _product_bid_change(totals) if totals.get("adsBidHistoryReady") else None
    if bid_change:
        diagnosis["conclusion"] = _bid_impact_conclusion(
            totals, diagnosis.get("conclusion")
        )
    lines = [_format_product_ads_diagnosis_block(diagnosis)]
    return "\n".join(lines)


def _build_top_drops_block(problem_products, summary_stats=None):
    return _build_product_movement_block(problem_products, "drop", summary_stats)


def _build_top_growth_block(problem_products):
    return _build_product_movement_block(problem_products, "growth")


def _build_stock_risks_block(records):
    stock_records = [
        record
        for record in records
        if record.get("daysUntilOOS") not in (None, "")
        or str(record.get("forecastType") or "").upper() == "OOS"
    ]
    by_sku = {}
    for record in stock_records:
        key = _problem_group_key(record)
        current = by_sku.get(key)
        if current is None or (_forecast_eta_hours(record) or 9999) < (
            _forecast_eta_hours(current) or 9999
        ):
            by_sku[key] = record
    records_unique = list(by_sku.values())
    less_1 = [r for r in records_unique if (_forecast_eta_hours(r) or 9999) < 24]
    less_3 = [r for r in records_unique if (_forecast_eta_hours(r) or 9999) < 72]
    critical = sorted(
        records_unique, key=lambda item: _forecast_eta_hours(item) or 9999
    )[:3]
    lines = [
        "📦 <b>Риски остатков</b>",
        f"Остатков менее 1 дня: {_format_number(len(less_1))} товаров",
        f"Остатков менее 3 дней: {_format_number(len(less_3))} товаров",
    ]
    if critical:
        lines.append("Самые критичные SKU:")
        for record in critical:
            eta = (
                _format_forecast_eta(record)
                or f"≈{_format_number(record.get('daysUntilOOS'))} дня"
            )
            lines.append(f"- {_format_product_identity(record)}: {eta}")
    return "\n".join(lines)


def _build_perfume_intelligence_block(summary_stats):
    return ""


def _build_executive_ads_block(records, summary_stats):
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

    problem_campaigns = ads_summary.get(
        "problemCampaigns", ads_summary.get("problems", len(ads_records))
    )
    if ads_records:
        ads_summary = {**ads_summary}
        ads_summary.setdefault(
            "problemSku",
            len({_problem_group_key(record) for record in ads_records}),
        )
        ads_summary.setdefault("problemSignals", len(ads_records))
    lines = _ads_summary_lines(ads_summary, summary_stats)

    if not ads_records:
        return "\n".join(lines)

    first_problem = ads_records[0]

    for label, key in (
        ("Лучший товар по рекламе", "bestSku"),
        ("Зона внимания", "worstSku"),
    ):
        sku = ads_summary.get(key) or {}
        if sku:
            lines.append("")
            lines.append(
                f"{label}:\n"
                f"{_format_product_identity(sku)} — "
                f"CTR {_format_number(sku.get('ctr'))}%, "
                f"CPC {_format_number(sku.get('cpc'))} ₽, "
                f"ДРР {_format_number(sku.get('drr'))}%"
            )

    if first_problem.get("problemType") == "AUCTION_OVERHEATING":
        lines.append("Вывод: аукцион перегрет, повышение ставок не дает роста позиций.")
    if problem_campaigns:
        lines.append("")
        lines.append(
            f"Есть {_format_number(problem_campaigns)} рекламных сигналов для проверки."
        )
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
        logistics_summary = logistics_text.replace("\n- ", "; ")
        return f"📦 <b>Остатки:</b> критичных сигналов нет\n{logistics_summary}"

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
        f"\n{_format_logistics_pipeline(pipeline).replace(chr(10) + '- ', '; ')}"
    )


def _best_worst_from_evidence(summary_stats):
    rows = [
        row
        for row in (summary_stats or {}).get("evidenceRows") or []
        if isinstance(row, dict)
    ]

    if not rows:
        return None, None

    growth_rows = [
        row
        for row in rows
        if to_number(row.get("orderSum_delta")) > 0
        and to_number(row.get("orderCount_delta")) > 0
    ]
    best = (
        max(
            growth_rows,
            key=lambda row: (
                to_number(row.get("orderSum_delta")),
                to_number(row.get("orderCount_delta")),
                to_number(row.get("openCount_delta")),
            ),
        )
        if growth_rows
        else None
    )
    worst = min(
        rows,
        key=lambda row: (
            to_number(row.get("orderSum_delta")),
            to_number(row.get("orderCount_delta")),
            to_number(row.get("openCount_delta")),
        ),
    )
    return best, worst


def _format_signal_sku(row):
    if not row:
        return "n/a"

    title = html.escape(str(row.get("title") or "Без названия"))
    nm_id = _format_wb_nm_id(row.get("nmId"))
    dynamic = html.escape(format_percent(row.get("orderSum_delta")))

    return f"{title} — WB {nm_id}, выручка {dynamic}"


def _build_no_problem_executive_block(summary_stats):
    return ""


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
    nm_id = _format_wb_nm_id(problem.get("nmId"))
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
        f" / товар {html.escape(str(problem.get('skuCriticality') or 'support'))}"
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
        nm_id = _format_wb_nm_id(record.get("nmId"), missing="—")
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
        -(_metric_priority_rank(record) * 1000)
        + to_number(record.get("severityScore"))
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
            f"{html.escape(str(record.get('title') or 'Товар'))} — {_format_wb_label(record.get('nmId'))}"
            for record in stock_impact_records[:3]
            if record.get("nmId")
        )
        if sku_list:
            check = (
                "Срочно пополнить остатки по товарам с заблокированной выручкой: "
                f"{sku_list}."
            )
            checks.append(check)
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
        technical_note = (
            "\nℹ️ Ограничение анализа: Недостаточно истории для точной оценки рекламы"
        )

    return (
        "🎯 <b>Что проверить в первую очередь:</b>\n"
        + "\n".join(lines)
        + technical_note
    )


def _is_predictive_problem(problem):
    return _is_forecast_signal(problem)


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
        if _has_clear_forecast_payload(record)
        and str(record.get("forecastType") or "").upper() not in {"OOS", "STOCK"}
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
    for record in stock_forecasts[:2]:
        eta = (
            _format_forecast_eta(record)
            or f"≈{_format_number(record.get('daysUntilOOS'))} дня"
        )
        lines.append(
            f"- {_format_product_identity(record)}: остатков хватит примерно на {eta}"
        )
    return "🔮 <b>Прогноз остатков:</b>\n" + "\n".join(lines)


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
            f"WB: {_format_wb_nm_id(row.get('nmId'))}\n\n"
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


def _build_api_coverage_debug_block(summary_stats):
    coverage = (summary_stats or {}).get("apiCoverage") or {}
    line = coverage.get("line")
    if not line:
        return ""

    parts = [html.escape(str(line))]
    return "\n".join(parts)


def _multi_seller_name(record):
    return str(
        record.get("sellerName")
        or record.get("seller_name")
        or record.get("seller")
        or SELLER_NAME
        or "Продавец без названия"
    )


def _multi_product_title(record):
    return html.escape(
        str(record.get("title") or record.get("subjectName") or "товар без названия")
    )


def _multi_problem_score(record):
    return (_business_impact_score(record), to_number(record.get("severityScore")))


def _multi_is_critical(record):
    return (
        str(record.get("severity") or "").lower() == "critical"
        or to_number(record.get("severityScore")) >= 70
    )



def _seller_result_name(result):
    return str(
        result.get("seller_name")
        or result.get("sellerName")
        or result.get("seller")
        or SELLER_NAME
        or "Продавец без названия"
    )


def _seller_result_processing_status(result):
    return str(result.get("processing_status") or result.get("processingStatus") or "")


def _seller_result_has_zero_stocks(result):
    return to_number(result.get("zero_stocks_count") or result.get("zeroStocksCount")) > 0


def _seller_result_is_critical(result):
    return (
        to_number(result.get("critical_problems_count") or result.get("criticalProblemsCount")) > 0
        or _seller_result_has_zero_stocks(result)
        or _seller_result_processing_status(result) == "failed"
    )


def _seller_result_needs_attention(result):
    return (
        to_number(result.get("warning_problems_count") or result.get("warningProblemsCount")) > 0
        or _seller_result_processing_status(result) in {"partial", "no_data"}
    )


def _seller_result_error_reason(result):
    return str(
        result.get("error_message")
        or result.get("errorMessage")
        or result.get("reason")
        or "данные не получены"
    )

def _build_multi_seller_brief(
    problems, summary_stats=None, top_limit=5, include_ads=True, include_stocks=True
):
    summary_stats = summary_stats or {}
    records = [
        record for record in _problems_to_records(problems) if isinstance(record, dict)
    ]
    seller_results = [
        result
        for result in summary_stats.get("sellerResults", [])
        if isinstance(result, dict)
    ]
    seller_names = set(summary_stats.get("sellerNames") or [])
    seller_names.update(_multi_seller_name(record) for record in records)
    seller_names.update(_seller_result_name(result) for result in seller_results)
    sellers_total = int(
        summary_stats.get("sellersTotal")
        or summary_stats.get("activeSellersCount")
        or len(seller_names)
        or len(seller_results)
        or 0
    )
    by_seller = {name: [] for name in seller_names}
    for record in records:
        by_seller.setdefault(_multi_seller_name(record), []).append(record)

    if seller_results:
        critical_sellers = sum(1 for result in seller_results if _seller_result_is_critical(result))
        warning_sellers = sum(
            1
            for result in seller_results
            if not _seller_result_is_critical(result)
            and _seller_result_needs_attention(result)
        )
        ok_sellers = sum(
            1
            for result in seller_results
            if _seller_result_processing_status(result) == "success"
            and to_number(result.get("critical_problems_count") or result.get("criticalProblemsCount")) == 0
            and to_number(result.get("warning_problems_count") or result.get("warningProblemsCount")) == 0
        )
    else:
        critical_sellers = sum(
            1
            for seller_records in by_seller.values()
            if any(_multi_is_critical(record) for record in seller_records)
        )
        warning_sellers = sum(
            1
            for seller_records in by_seller.values()
            if seller_records and not any(_multi_is_critical(record) for record in seller_records)
        )
        ok_sellers = max(sellers_total - critical_sellers - warning_sellers, 0)

    top_records = sorted(
        records, key=lambda record: _multi_problem_score(record), reverse=True
    )[:top_limit]
    lines = [
        "🌅 <b>WB Morning Brief</b>",
        "",
        "Период: вчера 00:00–24:00 МСК",
        "Сравнение: со средним за 3 дня",
        "",
        f"Проверено продавцов: {_format_number(sellers_total)}",
        "",
        f"🔴 Критичные: {_format_number(critical_sellers)}",
        f"🟡 Требуют внимания: {_format_number(warning_sellers)}",
        f"🟢 Без критичных проблем: {_format_number(ok_sellers)}",
    ]

    problem_seller_results = [
        result
        for result in seller_results
        if _seller_result_processing_status(result) in {"no_data", "failed"}
    ]
    if problem_seller_results:
        lines.extend(["", "⚠️ <b>Продавцы без данных / с ошибкой</b>", ""])
        for index, result in enumerate(problem_seller_results, start=1):
            lines.extend(
                [
                    f"{index}. {html.escape(_seller_result_name(result))}",
                    f"   Статус: {html.escape(_seller_result_processing_status(result))}",
                    f"   Причина: {html.escape(_seller_result_error_reason(result))}",
                    "",
                ]
            )
        if lines[-1] == "":
            lines.pop()

    if top_records:
        lines.extend(["", f"🔴 <b>ТОП-{len(top_records)} проблем дня</b>", ""])
        for index, record in enumerate(top_records, start=1):
            nm_id = html.escape(str(record.get("nmId") or record.get("nm_id") or "—"))
            problem_bits = []
            metric_label = get_problem_label(
                record.get("metric") or record.get("problemType") or ""
            )
            dynamic = record.get("dynamicPercent")
            if metric_label:
                problem_bits.append(metric_label)
            if dynamic not in (None, ""):
                problem_bits.append(f"{_format_number(dynamic)}%")
            if not problem_bits and record.get("problem"):
                problem_bits.append(str(record.get("problem")))
            diagnosis = (
                record.get("diagnosis")
                or record.get("rootCause")
                or record.get("recommendation")
                or record.get("problemName")
                or "проверить ключевой драйвер просадки"
            )
            lines.extend(
                [
                    f"{index}. Продавец: {html.escape(_multi_seller_name(record))}",
                    f"   Товар: {_multi_product_title(record)}",
                    f"   WB: {nm_id}",
                    "   Проблема: "
                    f"{html.escape(', '.join(problem_bits) or 'значимое отклонение')}",
                    f"   Диагноз: {html.escape(str(diagnosis))}",
                    "",
                ]
            )
        if lines[-1] == "":
            lines.pop()

    if include_ads:
        ads_records = [record for record in records if _is_ads_problem(record)]
        ads_summary = summary_stats.get("adsSummary") or {}
        total_sku = to_number(
            ads_summary.get("totalSku") or summary_stats.get("totalSkuFromApi")
        )
        advertised_sku = to_number(
            ads_summary.get("advertisedSku") or summary_stats.get("advertisedSkuCount")
        )
        if ads_records or ads_summary:
            red = len(
                {
                    _problem_group_key(record)
                    for record in ads_records
                    if _multi_is_critical(record)
                }
            )
            yellow = len(
                {
                    _problem_group_key(record)
                    for record in ads_records
                    if not _multi_is_critical(record)
                }
            )
            green = max(int(total_sku - red - yellow), 0) if total_sku else 0
            lines.extend([
                "", "📢 <b>Реклама</b>", "",
                f"Покрытие: {_format_number(advertised_sku)}/{_format_number(total_sku)} товаров",
                f"CTR: {_format_number(ads_summary.get('currentCtr'))}%",
                f"CPC: {_format_number(ads_summary.get('currentCpc'))} ₽",
                f"ДРР: {_format_number(ads_summary.get('currentDrr'))}%",
                "",
                f"🔴 Требует проверки: {_format_number(red)} товаров",
                f"🟡 Недостаточно данных: {_format_number(yellow)} товаров",
                f"🟢 Стабильно: {_format_number(green)} товаров",
            ])

    stock_records = [
        record
        for record in records
        if record.get("problemCategory") == "stocks"
        or record.get("metric") in ("wbStocks", "realSellableStock", "stocks")
    ]
    if include_stocks and stock_records:
        zero = len({_problem_group_key(record) for record in stock_records if to_number(record.get("currentValue") or record.get("stock") or record.get("wbStocks")) <= 0})
        risk = max(len({_problem_group_key(record) for record in stock_records}) - zero, 0)
        lines.extend(["", "📦 <b>Остатки</b>", "", f"🔴 Нулевые остатки: {_format_number(zero)} товаров", f"🟡 Риск OOS: {_format_number(risk)} товаров", "🟢 Без критичных проблем: 0 товаров"])

    first_sellers = sorted(
        ((seller, seller_records) for seller, seller_records in by_seller.items() if seller_records),
        key=lambda item: sum(_business_impact_score(record) for record in item[1]),
        reverse=True,
    )[:4]
    if first_sellers:
        lines.extend(["", "🎯 <b>Кого смотреть первым</b>", ""])
        for index, (seller, seller_records) in enumerate(first_sellers, start=1):
            critical_count = sum(1 for record in seller_records if _multi_is_critical(record))
            revenue_drop = sum(_absolute_metric_drop(record) for record in seller_records if record.get("metric") in ("orderSum", "revenue"))
            ads_attention = any(_is_ads_problem(record) for record in seller_records)
            stock_risk = any(record in stock_records for record in seller_records)
            reason_parts = []
            if critical_count:
                reason_parts.append(f"{_format_number(critical_count)} критичных товаров")
            if revenue_drop:
                reason_parts.append(f"просадка выручки {_format_number(revenue_drop)} ₽")
            if ads_attention:
                reason_parts.append("реклама требует проверки")
            if stock_risk:
                reason_parts.append("риск остатков")
            lines.extend([f"{index}. {html.escape(seller)}", f"   Причина: {', '.join(reason_parts) or 'есть значимые сигналы'}", ""])
        if lines[-1] == "":
            lines.pop()

    message = "\n".join(lines)
    diagnostic = ("TELEGRAM MULTI SELLER BRIEF:\n" f"sellers total: {sellers_total}\n" f"critical sellers: {critical_sellers}\n" f"warning sellers: {warning_sellers}\n" f"ok sellers: {ok_sellers}\n" f"top problems selected: {len(top_records)}\n" f"message length: {len(message)}")
    logger.info(diagnostic)
    print(diagnostic)
    return message


def _build_multi_seller_brief_limited(problems, summary_stats=None, max_length=3500):
    for top_limit, include_ads, include_stocks in ((5, True, True), (3, True, True), (3, False, True), (3, False, False)):
        message = _build_multi_seller_brief(problems, summary_stats, top_limit=top_limit, include_ads=include_ads, include_stocks=include_stocks)
        if len(sanitize_telegram_text(message)) <= max_length:
            return message
    return _trim_telegram_message(message, max_length=max_length)


def _build_telegram_message(problems, summary_stats=None, root_cause_insights=None):
    records = _problems_to_records(problems)
    has_positive_funnel_problem = any(
        _is_funnel_problem(record) and _business_impact_score(record) > 0
        for record in records
    )
    records = sorted(
        records,
        key=lambda record: (
            record.get("isSuppressed") is True,
            _business_sort_key(record, has_positive_funnel_problem),
        ),
    )
    factual_records = [
        record for record in records if _is_factual_executive_problem(record)
    ]
    priority_records = [
        record for record in factual_records if _is_priority_telegram_problem(record)
    ]
    critical_priority_records = [
        record
        for record in priority_records
        if _is_telegram_critical_block_problem(record)
    ]
    critical_factual_records = [
        record
        for record in factual_records
        if _is_telegram_critical_block_problem(record)
    ]
    main_records = critical_priority_records or critical_factual_records
    problem_products = _group_problems_by_product(main_records)
    trust_score = _report_trust_score(records)
    below_fact_count = len(
        {
            _problem_group_key(record)
            for record in factual_records
            if _is_below_abc_threshold(record)
        }
    )
    below_fact_line = (
        "⚠️ Есть фактические просадки по товарам ниже ABC-порога"
        if below_fact_count
        else ""
    )
    top_drop_products = _group_funnel_top_drop_products(records)
    top_drops_block = _build_top_drops_block(top_drop_products, summary_stats)
    top_drop_keys = (
        {_problem_group_key(product) for product in top_drop_products[:3]}
        if top_drops_block
        else set()
    )
    message_parts = [
        _build_executive_header(summary_stats),
        _build_executive_store_dynamics(summary_stats),
        f"Надежность оценки: {html.escape(_format_report_trust_score(trust_score))}",
        below_fact_line,
        _build_low_priority_signals_block(records),
        top_drops_block,
        _build_api_coverage_debug_block(summary_stats),
    ]

    if not main_records:
        message_parts.extend(
            [
                _build_perfume_intelligence_block(summary_stats),
                _build_executive_ads_block(priority_records, summary_stats),
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
            _build_executive_top_problems(
                problem_products, root_cause_insights, top_drop_keys
            ),
            _build_executive_ads_block(priority_records, summary_stats),
        ]
    )

    return _trim_telegram_message("\n\n".join(part for part in message_parts if part))


def _log_telegram_business_ranking(problems):
    records = _problems_to_records(problems)
    critical_records = [
        record
        for record in records
        if isinstance(record, dict)
        and _is_factual_executive_problem(record)
        and _is_telegram_critical_block_problem(record)
    ]
    top_problem = log_business_ranking(critical_records, source="telegram")
    diagnostic = (
        "TELEGRAM BUSINESS RANKING:\n"
        f"top nmId: {top_problem.get('nmId') or 'n/a'}\n"
        f"top title: {top_problem.get('title') or 'n/a'}\n"
        f"businessImpactScore: {top_problem.get('businessImpactScore') or 0}\n"
        f"isBelowAbcThreshold: {top_problem.get('isBelowAbcThreshold') or False}\n"
        f"metric: {top_problem.get('metric') or 'n/a'}"
    )
    logger.info(diagnostic)
    print(diagnostic)


def send_telegram_morning_brief(problems, summary_stats=None, root_cause_insights=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not configured")
        return False

    summary_stats = summary_stats or {}
    is_multi_seller = (
        int(summary_stats.get("sellersTotal") or summary_stats.get("activeSellersCount") or 0)
        > 1
    )
    if is_multi_seller:
        message = _build_multi_seller_brief_limited(problems, summary_stats)
    else:
        message = _build_telegram_message(
            problems,
            summary_stats=summary_stats,
            root_cause_insights=root_cause_insights,
        )
        _log_telegram_business_ranking(problems)
    url = TELEGRAM_API_URL.format(token=token)
    message_parts = split_telegram_message(sanitize_telegram_text(message))
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
