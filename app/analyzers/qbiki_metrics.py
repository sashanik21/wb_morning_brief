"""Qbiki-style unit economics and ads profitability layer."""

from app.analyzers.severity import to_number
from app.seller_config import SELLER_NAME

QBIKI_METRIC_FIELDS = (
    "ordersPer1000Impressions",
    "organicCR",
    "adsCR",
    "adsOrders",
    "adsImpressions",
    "adsCTR",
    "adsClicks",
    "cartConversion",
    "orderConversion",
    "avgAdBid",
    "adProfitPerOrder",
    "CPO",
    "DRR",
    "cleanDRR",
    "cleanMargin",
    "cleanMarginOrganic",
    "cleanMarginAds",
    "ROI",
    "wbStock",
    "daysOfStock",
)

ADS_PROFITABILITY_STATUSES = (
    "PROFITABLE_ADS",
    "UNPROFITABLE_ADS",
    "ADS_NEEDS_CONTROL",
    "ADS_PAUSE_IF_OOS",
)

LOW_DRR_THRESHOLD = 15
HIGH_CLEAN_DRR_THRESHOLD = 25
LOW_ADS_DEPENDENCY_THRESHOLD = 20
HIGH_ADS_DEPENDENCY_THRESHOLD = 60

STATUS_LABELS = {
    "PROFITABLE_ADS": "Реклама прибыльная",
    "UNPROFITABLE_ADS": "Реклама убыточная",
    "ADS_NEEDS_CONTROL": "Реклама требует контроля",
    "ADS_PAUSE_IF_OOS": "Остановить рекламу до восстановления остатков",
}

STATUS_CONCLUSIONS = {
    "PROFITABLE_ADS": "реклама прибыльная",
    "UNPROFITABLE_ADS": "реклама убыточная",
    "ADS_NEEDS_CONTROL": "реклама требует контроля",
    "ADS_PAUSE_IF_OOS": "остановить рекламу до восстановления остатков",
}

QBIKI_PROBLEM_LABELS = {
    "PROFITABLE_ADS": "Реклама прибыльная, но товар заканчивается",
    "UNPROFITABLE_ADS": "Реклама убыточная",
    "ADS_NEEDS_CONTROL": "Реклама требует контроля экономики",
    "ADS_PAUSE_IF_OOS": "Реклама активна при нулевом остатке",
}


def _is_present(value):
    return value not in (None, "") and str(value) != "nan"


def _pick(row, *keys):
    for key in keys:
        value = row.get(key)
        if _is_present(value):
            return value
    return None


def _has_active_ads(row):
    return any(
        to_number(row.get(metric)) > 0
        for metric in ("adsImpressions", "adsClicks", "adsOrders")
    )


def _product_key(row):
    nm_id = row.get("nmId") or row.get("nm_id")
    if _is_present(nm_id):
        return (
            str(int(to_number(nm_id))) if to_number(nm_id) is not None else str(nm_id)
        )
    return str(row.get("title") or row.get("vendorCode") or "")


def _merge_context(row, funnel_by_key=None, ads_by_key=None):
    merged = dict(row)
    key = _product_key(row)
    funnel = (funnel_by_key or {}).get(key) or {}
    ads = (ads_by_key or {}).get(key) or {}

    merged.setdefault("sellerName", SELLER_NAME)
    merged["nmId"] = _pick(merged, "nmId", "nm_id") or _pick(funnel, "nmId", "nm_id")
    merged["vendorCode"] = _pick(merged, "vendorCode", "vendor_code") or _pick(
        funnel, "vendorCode", "vendor_code"
    )
    merged["title"] = (
        _pick(merged, "title")
        or _pick(funnel, "title", "productName")
        or _pick(ads, "title", "campaignName")
    )
    merged["wbStock"] = _pick(merged, "wbStock") or _pick(
        funnel, "realSellableStock", "readyForSaleStock", "wbStocks"
    )
    merged["adsImpressions"] = _pick(merged, "adsImpressions") or _pick(
        ads, "impressions"
    )
    merged["adsClicks"] = _pick(merged, "adsClicks") or _pick(ads, "clicks")
    merged["adsOrders"] = _pick(merged, "adsOrders") or _pick(ads, "orders")
    merged["adsCTR"] = _pick(merged, "adsCTR") or _pick(ads, "ctr")
    merged["DRR"] = _pick(merged, "DRR") or _pick(ads, "drr")
    merged["avgAdBid"] = _pick(merged, "avgAdBid") or _pick(ads, "bid")
    return merged


def _index_by_nm_id(rows):
    indexed = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        key = _product_key(row)
        if key:
            indexed[key] = row
    return indexed


def evaluate_ads_profitability(row):
    """Return Qbiki profitability status for a normalized product metrics row."""
    wb_stock = to_number(row.get("wbStock"))
    days_of_stock = to_number(row.get("daysOfStock"))
    clean_margin_ads = to_number(row.get("cleanMarginAds"))
    cpo = to_number(row.get("CPO"))
    profit_per_order = to_number(row.get("adProfitPerOrder"))
    drr = to_number(row.get("DRR"))
    clean_drr = to_number(row.get("cleanDRR"))
    roi = to_number(row.get("ROI"))

    if wb_stock == 0 and _has_active_ads(row):
        return "ADS_PAUSE_IF_OOS"

    profitable = (
        drr <= LOW_DRR_THRESHOLD
        and cpo < profit_per_order
        and clean_margin_ads > 0
        and roi > 0
    )
    unprofitable = (
        clean_drr >= HIGH_CLEAN_DRR_THRESHOLD
        and cpo > profit_per_order
        and clean_margin_ads < 0
    )

    if profitable and days_of_stock is not None and days_of_stock <= 1:
        return "ADS_NEEDS_CONTROL"
    if profitable:
        return "PROFITABLE_ADS"
    if unprofitable:
        return "UNPROFITABLE_ADS"
    if _has_active_ads(row):
        return "ADS_NEEDS_CONTROL"
    return ""


def _insights(row, status):
    insights = []
    if status == "ADS_NEEDS_CONTROL" and to_number(row.get("cleanMarginAds")) > 0:
        insights.append("Реклама прибыльная, но товар заканчивается")
        insights.append("Остатки ограничивают прибыльную рекламу")
    if status == "UNPROFITABLE_ADS":
        insights.append("Реклама убыточная")
    if (
        to_number(row.get("cleanMarginOrganic")) < 0
        and to_number(row.get("cleanMarginAds")) > 0
    ):
        insights.append("Органика убыточна, реклама вытягивает товар")
    ads_orders = to_number(row.get("adsOrders"))
    total_orders_proxy = ads_orders
    orders_per_1000 = to_number(row.get("ordersPer1000Impressions"))
    if ads_orders > 0 and orders_per_1000 > 0:
        total_orders_proxy = max(ads_orders, orders_per_1000)
    ads_share = (ads_orders / total_orders_proxy * 100) if total_orders_proxy else 0
    if ads_share >= HIGH_ADS_DEPENDENCY_THRESHOLD or to_number(
        row.get("adsCR")
    ) > to_number(row.get("organicCR")):
        insights.append("Товар зависит от рекламы")
    if (
        ads_share <= LOW_ADS_DEPENDENCY_THRESHOLD
        and to_number(row.get("organicCR")) > 0
    ):
        insights.append("Товар хорошо продается без рекламы")
    return list(dict.fromkeys(insights))


def enrich_qbiki_metrics(rows, funnel_rows=None, ads_rows=None):
    funnel_by_key = _index_by_nm_id(funnel_rows)
    ads_by_key = _index_by_nm_id(ads_rows)
    enriched = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        metrics_row = _merge_context(
            row, funnel_by_key=funnel_by_key, ads_by_key=ads_by_key
        )
        for field in QBIKI_METRIC_FIELDS:
            metrics_row[field] = to_number(metrics_row.get(field))
        status = evaluate_ads_profitability(metrics_row)
        metrics_row["adsProfitabilityStatus"] = status
        metrics_row["adsProfitabilityConclusion"] = STATUS_CONCLUSIONS.get(status, "")
        metrics_row["qbikiInsights"] = _insights(metrics_row, status)
        enriched.append(metrics_row)
    return enriched


def build_qbiki_problems(metrics_rows):
    problems = []
    for row in metrics_rows or []:
        status = row.get("adsProfitabilityStatus")
        if status not in {"UNPROFITABLE_ADS", "ADS_NEEDS_CONTROL", "ADS_PAUSE_IF_OOS"}:
            continue
        if status == "ADS_NEEDS_CONTROL" and to_number(row.get("daysOfStock")) > 2:
            continue
        recommendation = "Проверить ставку, CPO и отключить неэффективные кампании."
        if status == "ADS_NEEDS_CONTROL":
            recommendation = (
                "Срочно пополнить остатки, чтобы не остановить прибыльную рекламу."
            )
        if status == "ADS_PAUSE_IF_OOS":
            recommendation = "Остановить рекламу до восстановления остатков."
        problems.append(
            {
                "sellerName": row.get("sellerName") or SELLER_NAME,
                "problemCategory": "ads",
                "problemType": status,
                "problemLabel": QBIKI_PROBLEM_LABELS.get(
                    status, STATUS_LABELS.get(status, status)
                ),
                "metric": (
                    "cleanMarginAds" if status == "UNPROFITABLE_ADS" else "daysOfStock"
                ),
                "selectedValue": (
                    row.get("cleanMarginAds")
                    if status == "UNPROFITABLE_ADS"
                    else row.get("daysOfStock")
                ),
                "pastValue": "",
                "dynamicPercent": "",
                "severity": "high" if status != "ADS_NEEDS_CONTROL" else "critical",
                "severityScore": 75 if status != "ADS_NEEDS_CONTROL" else 90,
                "nmId": row.get("nmId"),
                "vendorCode": row.get("vendorCode"),
                "title": row.get("title") or "Без названия",
                "drr": row.get("DRR"),
                "cleanDRR": row.get("cleanDRR"),
                "CPO": row.get("CPO"),
                "ROI": row.get("ROI"),
                "cleanMarginAds": row.get("cleanMarginAds"),
                "adProfitPerOrder": row.get("adProfitPerOrder"),
                "wbStock": row.get("wbStock"),
                "daysOfStock": row.get("daysOfStock"),
                "recommendation": recommendation,
            }
        )
    return problems
