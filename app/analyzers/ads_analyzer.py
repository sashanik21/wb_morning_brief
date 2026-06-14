from datetime import datetime
from pathlib import Path

import pandas as pd

from app.analyzers.severity import calculate_problem_severity
from app.seller_config import SELLER_NAME

REPORTS_DIR = Path("reports")
ADS_REPORT_COLUMNS = [
    "sellerName",
    "campaignId",
    "campaignName",
    "nmId",
    "vendorCode",
    "title",
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "orders",
    "ordersSum",
    "spend",
    "drr",
    "problemType",
    "recommendation",
    "baselineReliability",
    "bid",
    "avgPosition",
    "bidDelta",
    "positionDelta",
    "adsRootCause",
    "adsEfficiencyScore",
    "auctionTemperature",
]

CTR_LOW_THRESHOLD = 3
CPC_GROWTH_THRESHOLD = 15
CPM_GROWTH_THRESHOLD = 15
DRR_HIGH_THRESHOLD = 30
IMPRESSIONS_DROP_THRESHOLD = -20


ADS_PROBLEM_LABELS = {
    "ads_ctr_drop": "CTR рекламы падение",
    "ads_cpc_growth": "CPC рост",
    "ads_cpm_growth": "CPM рост",
    "ads_drr_growth": "ДРР рост",
    "ads_spend_without_orders": "расход есть, заказов нет",
    "ads_ctr_low": "CTR низкий",
    "ads_ineffective": "реклама неэффективна",
    "ads_stopped": "реклама отключилась",
    "ads_impressions_drop": "резкое падение показов рекламы",
    "ads_traffic_drop": "просадка рекламного трафика",
    "ads_reach_expensive": "реклама стала дороже, охват снизился",
    "NEW_ACTIVITY_DETECTED": "новая рекламная активность",
    "AUCTION_OVERHEATING": "перегрев рекламного аукциона",
    "ads_position_drop": "ухудшение рекламных позиций",
    "ads_query_waste": "waste spend по поисковому запросу",
}


ADS_RECOMMENDATIONS = {
    "ads_ctr_drop": "Проверить креатив, ставки и релевантность запросов: CTR рекламы снизился.",
    "ads_cpc_growth": "Проверить ставки и конкуренцию: клик стал дороже более чем на 15%.",
    "ads_cpm_growth": "Проверить CPM, места размещения и бюджет: тысяча показов стала дороже.",
    "ads_drr_growth": "Снизить неэффективные ставки или перераспределить бюджет: ДРР выше целевого уровня.",
    "ads_spend_without_orders": "Остановить или сузить кампанию до проверки: есть расход без заказов.",
    "ads_ctr_low": "Обновить креатив/заголовок и проверить релевантность трафика: CTR ниже 3%.",
    "ads_ineffective": "Сравнить расход с заказами и выручкой, отключить слабые SKU или кампании.",
    "ads_stopped": "Проверить статус кампании, дневной бюджет и баланс рекламного кабинета.",
    "ads_impressions_drop": "Проверить бюджет, ставки, статус кампании и доступность карточки.",
    "ads_traffic_drop": "Проверить охват рекламной кампании: падение CTR совпало с падением переходов.",
    "ads_reach_expensive": "Оптимизировать ставки: CPC вырос, расход/охват снизились и переходы просели.",
    "NEW_ACTIVITY_DETECTED": "Наблюдать за новой рекламной активностью до накопления истории.",
    "AUCTION_OVERHEATING": "Остановить рост ставок: CPC и ставка растут, CTR падает, позиция не улучшается.",
    "ads_position_drop": "Проверить ставку, релевантность и конкурентов: рекламная позиция ухудшилась.",
    "ads_query_waste": "Отключить или занизить ставку по запросам с расходом без заказов.",
}


def _to_number(value, default=0):
    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dynamic_percent(current_value, previous_value):
    previous_value = _to_number(previous_value)

    if previous_value == 0:
        return None

    return round((_to_number(current_value) - previous_value) / previous_value * 100, 2)


def _metric_key(metric, prefix):
    return f"{prefix}{metric[0].upper()}{metric[1:]}"


def _previous_value(row, metric):
    return row.get(_metric_key(metric, "previous"))


def _history_average(row, metric, days):
    keys = (
        f"avg_{metric}_{days}d",
        f"avg{days}d{metric[0].upper()}{metric[1:]}",
        f"avg{metric[0].upper()}{metric[1:]}{days}d",
    )
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _ads_baseline(row, metric):
    candidates = (
        ("avg_7d", _history_average(row, metric, 7), "HIGH"),
        ("avg_3d", _history_average(row, metric, 3), "MEDIUM"),
        ("previous_day", _previous_value(row, metric), "LOW"),
    )

    for baseline_type, value, reliability in candidates:
        if value not in (None, "") and _to_number(value) != 0:
            return value, baseline_type, reliability

    return None, "insufficient_history", "INSUFFICIENT_HISTORY"


def _metric_from_history(row, metric):
    aliases = {
        "avgPosition": ["avg_position", "avgPosition", "avgAdPosition"],
        "orders": ["orders", "orders_count", "ordersCount"],
        "revenue": ["revenue", "ordersSum", "orders_sum"],
    }
    for key in aliases.get(metric, [metric]):
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _average(values):
    numbers = [_to_number(value, None) for value in values if value not in (None, "")]
    numbers = [value for value in numbers if value is not None]
    if not numbers:
        return None
    return round(sum(numbers) / len(numbers), 2)


def enrich_ads_time_series(ads_rows, storage=None, seller_id=None):
    enriched_rows = []
    metrics = ("ctr", "cpc", "drr", "bid", "avgPosition")
    for row in ads_rows or []:
        enriched = row.copy()
        if seller_id is not None:
            enriched["seller_id"] = seller_id
        history = []
        if (
            storage
            and hasattr(storage, "get_ads_history")
            and row.get("campaignId") not in (None, "")
        ):
            history = storage.get_ads_history(
                seller_id, row.get("campaignId"), row.get("nmId"), 7
            )
        for metric in metrics:
            values = [_metric_from_history(item, metric) for item in history]
            previous = (
                values[0] if values else enriched.get(_metric_key(metric, "previous"))
            )
            if previous not in (None, ""):
                enriched[_metric_key(metric, "previous")] = previous
                enriched[f"previous_day_{metric}"] = previous
            avg3 = _average(values[:3])
            avg7 = _average(values[:7])
            if avg3 is not None:
                enriched[f"avg_{metric}_3d"] = avg3
            if avg7 is not None:
                enriched[f"avg_{metric}_7d"] = avg7
        enriched["bidDelta"] = _dynamic_percent(
            enriched.get("bid"), _previous_value(enriched, "bid")
        )
        previous_position = _previous_value(enriched, "avgPosition")
        if previous_position not in (None, ""):
            enriched["positionDelta"] = _to_number(
                enriched.get("avgPosition")
            ) - _to_number(previous_position)
        else:
            enriched["positionDelta"] = ""
        enriched_rows.append(enriched)
    if storage and hasattr(storage, "save_daily_ads_metrics"):
        storage.save_daily_ads_metrics(enriched_rows)
    return enriched_rows


def _ads_root_cause(problem_type, baseline_reliability):
    mapping = {
        "ads_ctr_drop": "CTR_DROP",
        "ads_cpc_growth": "CPC_OVERHEATING",
        "AUCTION_OVERHEATING": "CPC_OVERHEATING",
        "ads_position_drop": "POSITION_DROP",
        "ads_ineffective": "LOW_CONVERSION",
        "ads_spend_without_orders": "LOW_CONVERSION",
        "ads_query_waste": "QUERY_WASTE",
    }
    return mapping.get(problem_type) or (
        "INSUFFICIENT_DATA"
        if baseline_reliability == "INSUFFICIENT_HISTORY"
        else "BID_OVERPAY"
    )


def _auction_temperature(row):
    cpc_delta = _dynamic_percent(row.get("cpc"), _previous_value(row, "cpc")) or 0
    bid_delta = (
        row.get("bidDelta")
        or _dynamic_percent(row.get("bid"), _previous_value(row, "bid"))
        or 0
    )
    if cpc_delta > 50 or (cpc_delta > 20 and bid_delta > 20):
        return "OVERHEATED"
    if cpc_delta > 15 or bid_delta > 15:
        return "HOT"
    return "NORMAL"


def _ads_efficiency_score(row):
    score = 100
    score -= max((_to_number(row.get("drr")) - DRR_HIGH_THRESHOLD) * 1.5, 0)
    score -= max((CTR_LOW_THRESHOLD - _to_number(row.get("ctr"))) * 8, 0)
    score -= max(
        (_dynamic_percent(row.get("cpc"), _previous_value(row, "cpc")) or 0) * 0.5, 0
    )
    score -= max(_to_number(row.get("positionDelta")) * 2, 0)
    return round(max(min(score, 100), 0), 1)


def _has_activity(row):
    return any(
        _to_number(row.get(metric)) > 0 for metric in ("impressions", "clicks", "spend")
    )


def _is_insufficient_ads_baseline(row, *metrics):
    return any(
        _ads_baseline(row, metric)[2] == "INSUFFICIENT_HISTORY" for metric in metrics
    )


def _has_previous_ctr_base(row):
    return (
        _to_number(_previous_value(row, "impressions")) > 0
        or _to_number(_previous_value(row, "clicks")) > 0
    )


def _ads_problem(row, problem_type, metric, selected_value, past_value=None):
    baseline_value, baseline_type, baseline_reliability = _ads_baseline(row, metric)
    if past_value not in (None, ""):
        baseline_value = past_value
        baseline_type = "previous_day"
        baseline_reliability = (
            "LOW" if _to_number(past_value) != 0 else "INSUFFICIENT_HISTORY"
        )

    dynamic_percent = _dynamic_percent(selected_value, baseline_value)
    label = ADS_PROBLEM_LABELS[problem_type]

    severity_fields = calculate_problem_severity(
        metric, selected_value, baseline_value, dynamic_percent, row.get("ABC")
    )
    if baseline_reliability == "LOW":
        severity_fields["severityScore"] = round(
            severity_fields["severityScore"] * 0.5, 2
        )
        severity_fields["severity"] = "low"
    if baseline_reliability == "INSUFFICIENT_HISTORY":
        severity_fields["severityScore"] = 0
        severity_fields["severity"] = "low"

    return {
        "sellerName": row.get("sellerName") or SELLER_NAME,
        "problemCategory": "ads",
        "campaignId": row.get("campaignId") or "",
        "campaignName": row.get("campaignName") or "",
        "nmId": row.get("nmId") or "",
        "vendorCode": row.get("vendorCode") or "",
        "title": row.get("title") or row.get("campaignName") or "Без названия",
        "metric": metric,
        "problemType": problem_type,
        "problemLabel": label,
        "selectedValue": selected_value,
        "pastValue": baseline_value if baseline_value not in (None, "") else "",
        "baselineType": baseline_type,
        "baselineValue": baseline_value if baseline_value not in (None, "") else "",
        "baselineReliability": baseline_reliability,
        "rootCause": _ads_root_cause(problem_type, baseline_reliability),
        "adsRootCause": _ads_root_cause(problem_type, baseline_reliability),
        "dynamicPercent": dynamic_percent if dynamic_percent is not None else "",
        **severity_fields,
        "ctr": row.get("ctr", 0),
        "previousCtr": _previous_value(row, "ctr"),
        "cpc": row.get("cpc", 0),
        "previousCpc": _previous_value(row, "cpc"),
        "cpm": row.get("cpm", 0),
        "previousCpm": _previous_value(row, "cpm"),
        "spend": row.get("spend", 0),
        "previousSpend": _previous_value(row, "spend"),
        "clicks": row.get("clicks", 0),
        "previousClicks": _previous_value(row, "clicks"),
        "orders": row.get("orders", 0),
        "previousOrders": _previous_value(row, "orders"),
        "ordersSum": row.get("ordersSum", 0),
        "previousOrdersSum": _previous_value(row, "ordersSum"),
        "drr": row.get("drr", 0),
        "previousDrr": _previous_value(row, "drr"),
        "spendDelta": _dynamic_percent(row.get("spend"), _previous_value(row, "spend"))
        or "",
        "ctrDelta": _dynamic_percent(row.get("ctr"), _previous_value(row, "ctr")) or "",
        "cpcDelta": _dynamic_percent(row.get("cpc"), _previous_value(row, "cpc")) or "",
        "drrDelta": _dynamic_percent(row.get("drr"), _previous_value(row, "drr")) or "",
        "bid": row.get("bid", 0),
        "previousBid": _previous_value(row, "bid"),
        "bidDelta": row.get("bidDelta")
        or _dynamic_percent(row.get("bid"), _previous_value(row, "bid"))
        or "",
        "avgPosition": row.get("avgPosition") or row.get("avgAdPosition") or 0,
        "avgAdPosition": row.get("avgPosition") or row.get("avgAdPosition") or 0,
        "previousAvgPosition": _previous_value(row, "avgPosition"),
        "positionDelta": row.get("positionDelta") or "",
        "adsEfficiencyScore": _ads_efficiency_score(row),
        "auctionTemperature": _auction_temperature(row),
        "recommendation": ADS_RECOMMENDATIONS[problem_type],
    }


def _is_funnel_open_drop(row):
    dynamic = row.get("openCountDynamic")

    if dynamic not in (None, ""):
        return _to_number(dynamic) < 0

    current = row.get("openCount") or row.get("selectedOpenCount")
    previous = row.get("previousOpenCount") or row.get("pastOpenCount")

    return (_dynamic_percent(current, previous) or 0) < 0


def _is_funnel_order_not_growing(row):
    dynamic = row.get("orderCountDynamic")

    if dynamic not in (None, ""):
        return _to_number(dynamic) <= 0

    current = row.get("orderCount") or row.get("selectedOrderCount")
    previous = row.get("previousOrderCount") or row.get("pastOrderCount")

    return (_dynamic_percent(current, previous) or 0) <= 0


def _funnel_rows_by_nm_id(funnel_rows):
    if funnel_rows is None:
        return {}

    if isinstance(funnel_rows, pd.DataFrame):
        if funnel_rows.empty:
            return {}
        records = funnel_rows.to_dict("records")
    elif isinstance(funnel_rows, (list, tuple)):
        if len(funnel_rows) == 0:
            return {}
        records = funnel_rows
    else:
        records = []

    return {
        str(row.get("nmId")): row
        for row in records
        if row.get("nmId") not in (None, "")
    }


def _append_ads_funnel_links(problems, ads_row, funnel_row):
    if not funnel_row:
        return

    ctr_dynamic = _dynamic_percent(ads_row.get("ctr"), _previous_value(ads_row, "ctr"))
    cpc_dynamic = _dynamic_percent(ads_row.get("cpc"), _previous_value(ads_row, "cpc"))
    spend_dynamic = _dynamic_percent(
        ads_row.get("spend"), _previous_value(ads_row, "spend")
    )
    spend = _to_number(ads_row.get("spend"))
    previous_spend = _to_number(_previous_value(ads_row, "spend"))

    if ctr_dynamic is not None and ctr_dynamic < 0 and _is_funnel_open_drop(funnel_row):
        problems.append(
            _ads_problem(
                ads_row,
                "ads_traffic_drop",
                "ctr",
                ads_row.get("ctr", 0),
                _previous_value(ads_row, "ctr"),
            )
        )

    if (
        cpc_dynamic is not None
        and cpc_dynamic > 0
        and spend < previous_spend
        and _is_funnel_open_drop(funnel_row)
    ):
        problems.append(
            _ads_problem(
                ads_row,
                "ads_reach_expensive",
                "cpc",
                ads_row.get("cpc", 0),
                _previous_value(ads_row, "cpc"),
            )
        )

    if (
        spend_dynamic is not None
        and spend_dynamic > 0
        and _is_funnel_order_not_growing(funnel_row)
    ):
        problems.append(
            _ads_problem(
                ads_row,
                "ads_ineffective",
                "spend",
                ads_row.get("spend", 0),
                _previous_value(ads_row, "spend"),
            )
        )


def _enrich_budget_waste_risk(problem, funnel_row):
    if not funnel_row:
        return problem
    sellable = _to_number(
        funnel_row.get("realSellableStock")
        or funnel_row.get("readyForSaleStock")
        or funnel_row.get("wbStocks")
    )
    spend = _to_number(problem.get("spend"))
    clicks = _to_number(problem.get("clicks"))
    if sellable == 0 and (spend > 0 or clicks > 0):
        problem["budgetWasteRisk"] = True
        problem["realSellableStock"] = sellable
        problem["stockState"] = funnel_row.get("stockState") or problem.get(
            "stockState"
        )
        problem["recommendation"] = (
            "Приостановить или сократить рекламу до восстановления остатков."
        )
    return problem


def analyze_ads_problems(ads_rows, funnel_rows=None):
    problems = []
    funnel_by_nm_id = _funnel_rows_by_nm_id(funnel_rows)

    for row in ads_rows or []:
        ctr = _to_number(row.get("ctr"))
        cpc = _to_number(row.get("cpc"))
        cpm = _to_number(row.get("cpm"))
        drr = _to_number(row.get("drr"))
        spend = _to_number(row.get("spend"))
        orders = _to_number(row.get("orders"))
        impressions = _to_number(row.get("impressions"))
        clicks = _to_number(row.get("clicks"))
        ctr_baseline, _, _ = _ads_baseline(row, "ctr")
        cpc_baseline, _, _ = _ads_baseline(row, "cpc")
        cpm_baseline, _, _ = _ads_baseline(row, "cpm")
        impressions_baseline, _, _ = _ads_baseline(row, "impressions")
        ctr_dynamic = (
            _dynamic_percent(ctr, ctr_baseline) if _has_previous_ctr_base(row) else None
        )
        cpc_dynamic = (
            _dynamic_percent(cpc, cpc_baseline)
            if _to_number(_previous_value(row, "clicks")) > 0
            else None
        )
        cpm_dynamic = _dynamic_percent(cpm, cpm_baseline)
        impressions_dynamic = _dynamic_percent(impressions, impressions_baseline)
        bid_dynamic = row.get("bidDelta") or _dynamic_percent(
            row.get("bid"), _previous_value(row, "bid")
        )
        position_delta = _to_number(row.get("positionDelta"))

        if (
            bid_dynamic is not None
            and bid_dynamic > 0
            and (cpc_dynamic or 0) > 0
            and (ctr_dynamic or 0) < 0
            and position_delta >= 0
        ):
            problems.append(
                _ads_problem(
                    row, "AUCTION_OVERHEATING", "cpc", cpc, _previous_value(row, "cpc")
                )
            )

        if position_delta > 3:
            problems.append(
                _ads_problem(
                    row,
                    "ads_position_drop",
                    "avgPosition",
                    row.get("avgPosition"),
                    _previous_value(row, "avgPosition"),
                )
            )

        for query in row.get("searchQueries") or []:
            if (
                _to_number(query.get("spend")) > 0
                and _to_number(query.get("orders")) == 0
            ):
                query_row = {
                    **row,
                    **query,
                    "campaignName": f"{row.get('campaignName', '')} / {query.get('query', '')}",
                }
                problems.append(
                    _ads_problem(
                        query_row, "ads_query_waste", "spend", query.get("spend"), ""
                    )
                )

        if _has_activity(row) and _is_insufficient_ads_baseline(row, "ctr", "cpc"):
            problems.append(
                _ads_problem(row, "NEW_ACTIVITY_DETECTED", "ctr", ctr, None)
            )

        if ctr_dynamic is not None and ctr_dynamic < 0:
            problems.append(
                _ads_problem(
                    row, "ads_ctr_drop", "ctr", ctr, _previous_value(row, "ctr")
                )
            )

        if cpc_dynamic is not None and cpc_dynamic > CPC_GROWTH_THRESHOLD:
            problems.append(
                _ads_problem(
                    row, "ads_cpc_growth", "cpc", cpc, _previous_value(row, "cpc")
                )
            )

        if cpm_dynamic is not None and cpm_dynamic > CPM_GROWTH_THRESHOLD:
            problems.append(
                _ads_problem(
                    row, "ads_cpm_growth", "cpm", cpm, _previous_value(row, "cpm")
                )
            )

        if (
            drr > DRR_HIGH_THRESHOLD
            and not (orders == 0 and _to_number(row.get("ordersSum")) == 0)
            and not _is_insufficient_ads_baseline(row, "drr")
        ):
            problems.append(
                _ads_problem(row, "ads_drr_growth", "drr", drr, DRR_HIGH_THRESHOLD)
            )

        if spend > 0 and orders == 0:
            problems.append(
                _ads_problem(row, "ads_spend_without_orders", "orders", orders, "")
            )

        if (
            impressions > 0
            and clicks > 0
            and ctr < CTR_LOW_THRESHOLD
            and not _is_insufficient_ads_baseline(row, "ctr")
        ):
            problems.append(
                _ads_problem(row, "ads_ctr_low", "ctr", ctr, CTR_LOW_THRESHOLD)
            )

        if spend > 0 and (
            orders == 0
            or (
                drr > DRR_HIGH_THRESHOLD
                and not _is_insufficient_ads_baseline(row, "drr")
            )
        ):
            problems.append(_ads_problem(row, "ads_ineffective", "spend", spend, ""))

        if impressions == 0 and clicks == 0 and spend == 0:
            problems.append(
                _ads_problem(row, "ads_stopped", "impressions", impressions, "")
            )

        if (
            impressions_dynamic is not None
            and impressions_dynamic < IMPRESSIONS_DROP_THRESHOLD
        ):
            problems.append(
                _ads_problem(
                    row,
                    "ads_impressions_drop",
                    "impressions",
                    impressions,
                    _previous_value(row, "impressions"),
                )
            )

        funnel_row = funnel_by_nm_id.get(str(row.get("nmId")))
        _append_ads_funnel_links(problems, row, funnel_row)
        for problem in problems:
            if str(problem.get("nmId")) == str(row.get("nmId")):
                _enrich_budget_waste_risk(problem, funnel_row)

    print(f"Ads problems found: {len(problems)}")
    return problems


def build_ads_summary(ads_rows, ads_problems):
    campaign_ids = {
        row.get("campaignId")
        for row in ads_rows or []
        if row.get("campaignId") not in (None, "")
    }
    problem_campaign_ids = {
        problem.get("campaignId")
        for problem in ads_problems or []
        if problem.get("campaignId") not in (None, "")
    }

    first_row = next((row for row in ads_rows or [] if isinstance(row, dict)), {})

    best_sku = max(
        ads_rows or [],
        key=lambda row: (_to_number(row.get("orders")), -_to_number(row.get("drr"))),
        default={},
    )
    worst_sku = max(
        ads_rows or [],
        key=lambda row: (_to_number(row.get("spend")), _to_number(row.get("drr"))),
        default={},
    )
    overheating = [
        problem
        for problem in ads_problems or []
        if problem.get("problemType") == "AUCTION_OVERHEATING"
    ]

    return {
        "activeCampaigns": len(campaign_ids),
        "adsRows": len(ads_rows or []),
        "problemCampaigns": len(problem_campaign_ids),
        "problems": len(ads_problems or []),
        "selectedPeriod": first_row.get("selectedPeriod")
        or first_row.get("date")
        or "",
        "pastPeriod": first_row.get("pastPeriod") or "",
        "adsEfficiencyScore": _average(
            [
                row.get("adsEfficiencyScore") or _ads_efficiency_score(row)
                for row in ads_rows or []
            ]
        ),
        "auctionTemperature": max(
            [_auction_temperature(row) for row in ads_rows or []] or ["NORMAL"],
            key={"NORMAL": 0, "HOT": 1, "OVERHEATED": 2}.get,
        ),
        "bestSku": best_sku,
        "worstSku": worst_sku,
        "overheatedCampaigns": len(overheating),
    }


def _first_problem_for_campaign(ads_problems):
    problems_by_campaign = {}

    for problem in ads_problems or []:
        campaign_id = problem.get("campaignId")

        if campaign_id not in problems_by_campaign:
            problems_by_campaign[campaign_id] = problem

    return problems_by_campaign


def build_ads_report_rows(ads_rows, ads_problems):
    problems_by_campaign = _first_problem_for_campaign(ads_problems)
    report_rows = []

    for row in ads_rows or []:
        problem = problems_by_campaign.get(row.get("campaignId"), {})
        report_rows.append(
            {
                "sellerName": row.get("sellerName") or SELLER_NAME,
                "campaignId": row.get("campaignId") or "",
                "campaignName": row.get("campaignName") or "",
                "nmId": row.get("nmId") or "",
                "vendorCode": row.get("vendorCode") or "",
                "title": row.get("title") or "",
                "impressions": row.get("impressions", 0),
                "clicks": row.get("clicks", 0),
                "ctr": row.get("ctr", 0),
                "cpc": row.get("cpc", 0),
                "cpm": row.get("cpm", 0),
                "orders": row.get("orders", 0),
                "ordersSum": row.get("ordersSum", 0),
                "spend": row.get("spend", 0),
                "drr": row.get("drr", 0),
                "bid": row.get("bid", 0),
                "avgPosition": row.get("avgPosition", 0),
                "bidDelta": row.get("bidDelta", ""),
                "positionDelta": row.get("positionDelta", ""),
                "adsRootCause": problem.get("adsRootCause") or "",
                "adsEfficiencyScore": row.get("adsEfficiencyScore")
                or _ads_efficiency_score(row),
                "auctionTemperature": row.get("auctionTemperature")
                or _auction_temperature(row),
                "problemType": problem.get("problemLabel") or "",
                "recommendation": problem.get("recommendation") or "",
                "baselineReliability": problem.get("baselineReliability") or "",
            }
        )

    return report_rows


def save_ads_report(ads_rows, ads_problems):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_date = datetime.now().date().strftime("%Y_%m_%d")
    report_path = REPORTS_DIR / f"ads_{report_date}.xlsx"
    dataframe = pd.DataFrame(build_ads_report_rows(ads_rows, ads_problems))

    if dataframe.empty:
        dataframe = pd.DataFrame(columns=ADS_REPORT_COLUMNS)
    else:
        dataframe = dataframe.reindex(columns=ADS_REPORT_COLUMNS)

    with pd.ExcelWriter(report_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, sheet_name="ads", index=False)

    return report_path
