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


def _previous_value(row, metric):
    return row.get(f"previous{metric[0].upper()}{metric[1:]}")


def _ads_problem(row, problem_type, metric, selected_value, past_value=None):
    dynamic_percent = _dynamic_percent(selected_value, past_value)
    label = ADS_PROBLEM_LABELS[problem_type]

    severity_fields = calculate_problem_severity(
        metric, selected_value, past_value, dynamic_percent, row.get("ABC")
    )

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
        "pastValue": past_value if past_value not in (None, "") else "",
        "dynamicPercent": dynamic_percent if dynamic_percent is not None else "",
        **severity_fields,
        "ctr": row.get("ctr", 0),
        "cpc": row.get("cpc", 0),
        "cpm": row.get("cpm", 0),
        "spend": row.get("spend", 0),
        "orders": row.get("orders", 0),
        "ordersSum": row.get("ordersSum", 0),
        "drr": row.get("drr", 0),
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
        ctr_dynamic = _dynamic_percent(ctr, _previous_value(row, "ctr"))
        cpc_dynamic = _dynamic_percent(cpc, _previous_value(row, "cpc"))
        cpm_dynamic = _dynamic_percent(cpm, _previous_value(row, "cpm"))
        impressions_dynamic = _dynamic_percent(
            impressions, _previous_value(row, "impressions")
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

        if drr > DRR_HIGH_THRESHOLD:
            problems.append(
                _ads_problem(row, "ads_drr_growth", "drr", drr, DRR_HIGH_THRESHOLD)
            )

        if spend > 0 and orders == 0:
            problems.append(
                _ads_problem(row, "ads_spend_without_orders", "orders", orders, "")
            )

        if impressions > 0 and clicks > 0 and ctr < CTR_LOW_THRESHOLD:
            problems.append(
                _ads_problem(row, "ads_ctr_low", "ctr", ctr, CTR_LOW_THRESHOLD)
            )

        if spend > 0 and (orders == 0 or drr > DRR_HIGH_THRESHOLD):
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

        _append_ads_funnel_links(
            problems, row, funnel_by_nm_id.get(str(row.get("nmId")))
        )

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

    return {
        "activeCampaigns": len(campaign_ids),
        "problemCampaigns": len(problem_campaign_ids),
        "problems": len(ads_problems or []),
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
                "problemType": problem.get("problemLabel") or "",
                "recommendation": problem.get("recommendation") or "",
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
