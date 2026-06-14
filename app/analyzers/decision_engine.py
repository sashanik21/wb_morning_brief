"""Business decision layer for executive prioritisation and noise suppression."""

from app.analyzers.severity import to_number

ACTION_PRIORITIES = ("NOW", "TODAY", "THIS_WEEK", "MONITOR", "IGNORE")
SKU_CRITICALITIES = ("flagship", "strategic", "support", "low_value", "dead_sku")

DERIVATIVE_METRICS_AFTER_OOS = {
    "addToCartPercent",
    "cartCount",
    "cartToOrderPercent",
    "ctr",
    "openCount",
    "orderCount",
    "avgPosition",
    "position",
}
ADS_CLUSTER_TYPES = {
    "ads_ctr_drop",
    "ads_cpc_growth",
    "ads_cpm_growth",
    "ads_drr_growth",
    "ads_ctr_low",
    "ads_ineffective",
    "ads_position_drop",
    "AUCTION_OVERHEATING",
}


def _records(data):
    if data is None:
        return []
    if hasattr(data, "to_dict"):
        return data.to_dict("records")
    if isinstance(data, list):
        return data
    return []


def _is_present(value):
    return value not in (None, "") and str(value) != "nan"


def _normalize_nm_id(value):
    if value in (None, ""):
        return ""
    number = to_number(value)
    if number is not None and float(number).is_integer():
        return str(int(number))
    return str(value).strip()


def _group_key(record):
    nm_id = _normalize_nm_id(record.get("nmId"))
    if nm_id:
        return ("nmId", nm_id)
    return ("title", str(record.get("title") or "Без названия"))


def _problem_revenue_loss(problem):
    return to_number(problem.get("potentialRevenueLoss") or problem.get("lostOrderSum"))


def _problem_lost_orders(problem):
    return to_number(problem.get("lostOrders") or problem.get("potentialOrdersLoss"))


def _decline_source(problem):
    return str(problem.get("declineSource") or problem.get("decline_source") or "")


def _is_ads_problem(problem):
    problem_type = str(problem.get("problemType") or "")
    return problem.get("problemCategory") == "ads" or problem_type.startswith("ads_")


def _is_stock_problem(problem):
    metric = problem.get("metric")
    problem_type = problem.get("problemType")
    return (
        problem.get("problemCategory") == "stocks"
        or problem_type in {"sellableOutOfStock", "OOS_FORECAST"}
        or metric in {"wbStocks", "realSellableStock", "stocks"}
        or _decline_source(problem) == "STOCK_DECLINE"
    )


def _is_oos_problem(problem):
    sellable = problem.get("realSellableStock")
    if sellable in (None, ""):
        sellable = problem.get("selectedValue")
    return _is_stock_problem(problem) and to_number(sellable) == 0


def _sku_criticality(problem):
    abc = str(problem.get("ABC") or "").upper()
    revenue_loss = _problem_revenue_loss(problem)
    orders_loss = _problem_lost_orders(problem)
    severity = str(problem.get("severity") or "").lower()
    score = to_number(problem.get("severityScore"))

    if abc == "A" or revenue_loss >= 10000 or orders_loss >= 10:
        return "flagship"
    if abc == "B" or revenue_loss >= 3000 or severity in {"critical", "high"}:
        return "strategic"
    if abc == "C" or score >= 25:
        return "support"
    if abc in {"D", "E"} or (revenue_loss == 0 and orders_loss == 0 and score < 15):
        return "low_value"
    return "dead_sku" if problem.get("isBelowAbcThreshold") else "support"


def _score_problem(problem):
    revenue_loss = _problem_revenue_loss(problem)
    lost_orders = _problem_lost_orders(problem)
    severity_score = to_number(problem.get("severityScore"))
    dynamic = abs(to_number(problem.get("dynamicPercent")) or 0)
    criticality = _sku_criticality(problem)

    role_bonus = {
        "flagship": 35,
        "strategic": 24,
        "support": 12,
        "low_value": -15,
        "dead_sku": -40,
    }.get(criticality, 0)
    score = min(revenue_loss / 350, 45) + min(lost_orders * 3, 30)
    score += min(severity_score / 2, 45) + min(dynamic / 3, 20) + role_bonus

    if str(problem.get("organicImportance") or "").upper() in {"HIGH", "A", "TRUE"}:
        score += 12
    if _is_ads_problem(problem):
        score += 10
    if problem.get("adsDependency") or _decline_source(problem) == "ADS_DECLINE":
        score += 8
    if problem.get("forecastType") or str(problem.get("problemType") or "").endswith(
        "_FORECAST"
    ):
        score += 12
    if _is_oos_problem(problem):
        score += 35
    if problem.get("searchVisibilityRisk"):
        score += 10
    if str(problem.get("categoryImportance") or "").upper() in {"HIGH", "A", "TOP"}:
        score += 10
    if problem.get("baselineReliability") == "INSUFFICIENT_HISTORY":
        score -= 25
    if problem.get("isBelowAbcThreshold"):
        score -= 30
    return max(0, round(score, 1))


def _action_priority(score, problem):
    if problem.get("isBelowAbcThreshold") or _sku_criticality(problem) == "dead_sku":
        return "IGNORE"
    if _is_oos_problem(problem) and score >= 60:
        return "NOW"
    if score >= 85:
        return "NOW"
    if score >= 55:
        return "TODAY"
    if score >= 30:
        return "THIS_WEEK"
    return "MONITOR"


def _root_chain(problem):
    if _is_oos_problem(problem):
        return ["Нет остатков", "Потеря позиций", "Падение CTR", "Падение заказов"]
    if _is_ads_problem(problem):
        return [
            "Реклама стала менее эффективной",
            "Рост стоимости трафика",
            "Падение заказов",
        ]
    if problem.get("searchVisibilityRisk") or problem.get("metric") == "openCount":
        return ["Потеря органической видимости", "Меньше переходов", "Падение заказов"]
    return [
        problem.get("problemLabel") or problem.get("problemType") or "Сигнал",
        "Риск потери заказов",
    ]


def _cluster_label(problem):
    if _is_ads_problem(problem) and problem.get("problemType") in ADS_CLUSTER_TYPES:
        return "Реклама стала менее эффективной."
    if _is_stock_problem(problem):
        return "Проблема доступности товара."
    if problem.get("searchVisibilityRisk"):
        return "Органическая видимость ухудшается."
    return (
        problem.get("problemLabel")
        or problem.get("problemType")
        or "Проблема требует проверки."
    )


def _suppression_reason(problem, product_has_oos, seen_clusters):
    if (
        product_has_oos
        and (
            problem.get("metric") in DERIVATIVE_METRICS_AFTER_OOS
            or _is_ads_problem(problem)
        )
        and not _is_oos_problem(problem)
    ):
        return "secondary_derivative_after_oos"
    cluster = _cluster_label(problem)
    if cluster in seen_clusters and _score_problem(problem) < 35:
        return "weak_duplicated_signal"
    if (
        _is_ads_problem(problem)
        and (to_number(problem.get("ctr")) or 0) > 0
        and _score_problem(problem) < 25
    ):
        return "low_value_ctr_noise"
    return ""


def apply_decision_engine(problems):
    """Return problems enriched with decision fields used by storage, tasks, and Telegram."""
    records = [
        dict(record) for record in _records(problems) if isinstance(record, dict)
    ]
    grouped = {}
    for record in records:
        grouped.setdefault(_group_key(record), []).append(record)

    for product_records in grouped.values():
        product_has_oos = any(_is_oos_problem(record) for record in product_records)
        seen_clusters = set()
        for record in sorted(product_records, key=_score_problem, reverse=True):
            score = _score_problem(record)
            record["skuCriticality"] = _sku_criticality(record)
            record["businessPriorityScore"] = score
            record["actionPriority"] = _action_priority(score, record)
            record["rootCauseChain"] = " → ".join(_root_chain(record))
            record["signalCluster"] = _cluster_label(record)
            reason = _suppression_reason(record, product_has_oos, seen_clusters)
            record["isSuppressed"] = bool(reason)
            record["suppressionReason"] = reason
            if not reason:
                seen_clusters.add(record["signalCluster"])
            if record["actionPriority"] == "IGNORE":
                record["isSuppressed"] = True
                record["suppressionReason"] = (
                    record["suppressionReason"] or "ignored_low_business_value"
                )

    return sorted(
        records,
        key=lambda record: (
            record.get("isSuppressed") is True,
            -to_number(record.get("businessPriorityScore")),
            str(record.get("title") or ""),
        ),
    )
