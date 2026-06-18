from datetime import date
from pathlib import Path

import pandas as pd

REPORTS_DIR = Path("reports")

COVERAGE_COLUMNS = [
    "sellerName",
    "nmId",
    "vendorCode",
    "title",
    "inCardsApi",
    "inProductsCatalog",
    "inFunnelApi",
    "inAdsApi",
    "inSuppliesApi",
    "inProblems",
    "inTelegramTop",
    "hasFunnelMetrics",
    "hasAdsMetrics",
    "hasSupplyMetrics",
    "hasForecast",
    "hasBusinessImpact",
    "funnelFieldsFilled",
    "adsFieldsFilled",
    "supplyFieldsFilled",
    "problemCount",
    "adsProblemCount",
    "funnelProblemCount",
]

FUNNEL_METRIC_FIELDS = [
    "openCount",
    "cartCount",
    "orderCount",
    "orderSum",
    "addToCartPercent",
    "cartToOrderPercent",
    "wbStocks",
    "mpStocks",
    "realSellableStock",
]

ADS_METRIC_FIELDS = [
    "impressions",
    "clicks",
    "ctr",
    "cpc",
    "cpm",
    "spend",
    "orders",
    "ordersCount",
    "ordersSum",
    "revenue",
    "drr",
    "bid",
]

SUPPLY_METRIC_FIELDS = [
    "incomingStock",
    "acceptanceStock",
    "transitStock",
    "readyForSaleStock",
    "returningStock",
    "realSellableStock",
]

BUSINESS_IMPACT_FIELDS = [
    "lostOrders",
    "lostOrderSum",
    "potentialRevenueLoss",
    "potentialOrdersLoss",
    "blockedRevenuePerDay",
    "blockedOrdersPerDay",
]

FORECAST_FIELDS = [
    "forecastEtaHours",
    "daysUntilOOS",
    "forecastType",
    "forecastMessage",
]


def _first_present(row, keys, default=""):
    if not isinstance(row, dict):
        return default

    for key in keys:
        value = row.get(key)

        if value not in (None, ""):
            return value

    return default


def _nm_id(row):
    value = _first_present(row, ["nmId", "nmID", "nm_id", "nm"], None)

    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _product_container(row):
    if not isinstance(row, dict):
        return {}

    product = row.get("product")

    if isinstance(product, dict):
        return {**row, **product}

    return row


def _records_by_nm_id(rows):
    result = {}

    for row in rows or []:
        if not isinstance(row, dict):
            continue

        nm_id = _nm_id(row)

        if nm_id is not None:
            result.setdefault(nm_id, []).append(row)

    return result


def _products_by_nm_id(rows):
    result = {}

    for row in rows or []:
        product = _product_container(row)
        nm_id = _nm_id(product)

        if nm_id is not None:
            result[nm_id] = product

    return result


def _value_filled(value):
    return value not in (None, "")


def _fields_filled(rows, fields):
    seen = set()

    for row in rows or []:
        for field in fields:
            if _value_filled(_first_present(row, [field, _snake_case(field)], None)):
                seen.add(field)

    return len(seen)


def _snake_case(name):
    chars = []

    for char in name:
        if char.isupper() and chars:
            chars.append("_")

        chars.append(char.lower())

    return "".join(chars)


def _problem_category(problem):
    category = str(
        _first_present(problem, ["problemCategory", "problem_category"], "")
    ).lower()
    problem_type = str(
        _first_present(problem, ["problemType", "problem_type"], "")
    ).lower()
    metric = (
        str(problem.get("metric") or "").lower() if isinstance(problem, dict) else ""
    )

    if category:
        return category

    if problem_type.startswith("ads_") or metric in {"ctr", "cpc", "drr", "spend"}:
        return "ads"

    return "funnel"


def _has_any(rows, fields):
    return _fields_filled(rows, fields) > 0


def _print_sample_rows(title, dataframe, limit=5):
    if dataframe is None or dataframe.empty:
        return

    print(title)

    for row in dataframe.head(limit).to_dict("records"):
        print(f"  nmID={row.get('nmId')} title={row.get('title') or ''}")


def build_api_coverage_report(
    *,
    seller_name,
    cards,
    products,
    funnel_rows,
    ads_rows,
    supply_stock_metrics_by_nm_id,
    problems,
    ads_api_partial=False,
    qbiki_source_status="not configured",
    ads_matching_debug=None,
):
    cards_by_nm = _products_by_nm_id(cards)
    products_by_nm = _products_by_nm_id(products)
    funnel_by_nm = _records_by_nm_id(funnel_rows)

    ads_rows_count = len([row for row in ads_rows or [] if isinstance(row, dict)])
    ads_campaign_ids = {
        _first_present(row, ["campaignId", "advertId", "adsCampaignId", "id"], None)
        for row in ads_rows or []
        if isinstance(row, dict)
    }
    ads_campaign_ids.discard(None)

    ads_by_nm = _records_by_nm_id(ads_rows)

    supply_by_nm = {}

    for nm_id, metrics in (supply_stock_metrics_by_nm_id or {}).items():
        try:
            normalized_nm_id = int(float(str(nm_id)))
        except (TypeError, ValueError):
            continue

        if metrics is not None:
            supply_by_nm[normalized_nm_id] = [metrics]

    problems_by_nm = _records_by_nm_id(problems)
    telegram_nm_ids = set(problems_by_nm.keys())

    rows = []

    for nm_id in sorted(cards_by_nm):
        card = cards_by_nm[nm_id]
        product = products_by_nm.get(nm_id, {})
        funnel_records = funnel_by_nm.get(nm_id, [])
        ads_records = ads_by_nm.get(nm_id, [])
        supply_records = supply_by_nm.get(nm_id, [])
        problem_records = problems_by_nm.get(nm_id, [])

        ads_problem_count = sum(
            1 for problem in problem_records if _problem_category(problem) == "ads"
        )
        funnel_problem_count = sum(
            1 for problem in problem_records if _problem_category(problem) == "funnel"
        )

        rows.append(
            {
                "sellerName": seller_name,
                "nmId": nm_id,
                "vendorCode": _first_present(card, ["vendorCode", "vendor_code"])
                or _first_present(product, ["vendorCode", "vendor_code"]),
                "title": _first_present(
                    card, ["title", "productName", "product_name", "name"]
                )
                or _first_present(
                    product, ["title", "productName", "product_name", "name"]
                ),
                "inCardsApi": True,
                "inProductsCatalog": nm_id in products_by_nm,
                "inFunnelApi": nm_id in funnel_by_nm,
                "inAdsApi": nm_id in ads_by_nm,
                "inSuppliesApi": nm_id in supply_by_nm,
                "inProblems": nm_id in problems_by_nm,
                "inTelegramTop": nm_id in telegram_nm_ids,
                "hasFunnelMetrics": _has_any(funnel_records, FUNNEL_METRIC_FIELDS),
                "hasAdsMetrics": _has_any(ads_records, ADS_METRIC_FIELDS),
                "hasSupplyMetrics": _has_any(supply_records, SUPPLY_METRIC_FIELDS),
                "hasForecast": _has_any(problem_records, FORECAST_FIELDS),
                "hasBusinessImpact": _has_any(problem_records, BUSINESS_IMPACT_FIELDS),
                "funnelFieldsFilled": _fields_filled(
                    funnel_records, FUNNEL_METRIC_FIELDS
                ),
                "adsFieldsFilled": _fields_filled(ads_records, ADS_METRIC_FIELDS),
                "supplyFieldsFilled": _fields_filled(
                    supply_records, SUPPLY_METRIC_FIELDS
                ),
                "problemCount": len(problem_records),
                "adsProblemCount": ads_problem_count,
                "funnelProblemCount": funnel_problem_count,
            }
        )

    coverage = pd.DataFrame(rows, columns=COVERAGE_COLUMNS)

    missing_ads = coverage[~coverage["inAdsApi"]][
        ["sellerName", "nmId", "vendorCode", "title"]
    ]
    missing_supplies = coverage[~coverage["inSuppliesApi"]][
        ["sellerName", "nmId", "vendorCode", "title"]
    ]

    api_status = pd.DataFrame(
        [
            {
                "source": "Cards API",
                "status": "OK",
                "products": len(cards_by_nm),
                "warning": "",
            },
            {
                "source": "Funnel API",
                "status": "OK",
                "products": len(funnel_by_nm),
                "warning": "",
            },
            {
                "source": "Ads API",
                "status": (
                    "PARTIAL"
                    if ads_api_partial or len(ads_by_nm) < len(cards_by_nm)
                    else "OK"
                ),
                "products": len(ads_by_nm),
                "adsCampaignCount": len(ads_campaign_ids),
                "adsRowsCount": ads_rows_count,
                "warning": "429 warning" if ads_api_partial else "",
            },
            {
                "source": "Supplies API",
                "status": "OK",
                "products": len(supply_by_nm),
                "warning": "",
            },
            {
                "source": "Qbiki",
                "status": (
                    "disabled"
                    if qbiki_source_status == "not configured"
                    else qbiki_source_status
                ),
                "products": "",
                "warning": "",
            },
        ]
    )

    ads_matching_debug_frame = pd.DataFrame(
        ads_matching_debug or [],
        columns=[
            "campaignId",
            "campaignName",
            "advertId",
            "apiNmId",
            "matchedNmId",
            "matchedVendorCode",
            "matchStrategy",
            "matchConfidence",
            "matchStatus",
            "reason",
        ],
    )

    return {
        "coverage": coverage,
        "missing_ads": missing_ads,
        "missing_supplies": missing_supplies,
        "api_status": api_status,
        "ads_matching_debug": ads_matching_debug_frame,
        "adsCampaignCount": len(ads_campaign_ids),
        "adsRowsCount": ads_rows_count,
        "adsUniqueNmids": len(ads_by_nm),
    }


def save_api_coverage_report(report):
    REPORTS_DIR.mkdir(exist_ok=True)
    path = REPORTS_DIR / f"api_coverage_{date.today().strftime('%Y_%m_%d')}.xlsx"

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for sheet_name in [
            "coverage",
            "missing_ads",
            "missing_supplies",
            "api_status",
            "ads_matching_debug",
        ]:
            report[sheet_name].to_excel(writer, sheet_name=sheet_name, index=False)

    return path


def print_api_coverage_summary(report):
    coverage = report["coverage"]

    cards_count = int(coverage["inCardsApi"].sum())
    funnel_count = int(coverage["inFunnelApi"].sum())
    ads_count = int(coverage["inAdsApi"].sum())
    supplies_count = int(coverage["inSuppliesApi"].sum())
    problems_count = int(coverage["inProblems"].sum())
    telegram_top_count = int(coverage["inTelegramTop"].sum())

    missing_ads_count = len(report["missing_ads"])
    missing_supplies_count = len(report["missing_supplies"])

    print(
        "API COVERAGE SUMMARY: "
        f"cards={cards_count} "
        f"funnel={funnel_count} "
        f"ads={ads_count} "
        f"supplies={supplies_count} "
        f"problems={problems_count} "
        f"telegram_top={telegram_top_count}"
    )

    print(
        "ADS COVERAGE: "
        f"campaigns={report.get('adsCampaignCount', 0)} "
        f"ads_rows={report.get('adsRowsCount', 0)} "
        f"unique_nmIds={report.get('adsUniqueNmids', ads_count)}"
    )

    print(
        "MISSING DATA SUMMARY: "
        f"ads_missing={missing_ads_count} "
        f"supplies_missing={missing_supplies_count}"
    )

    _print_sample_rows("ADS MISSING SAMPLE:", report["missing_ads"])
    _print_sample_rows("SUPPLIES MISSING SAMPLE:", report["missing_supplies"])


def coverage_summary_line(report):
    coverage = report["coverage"]
    total = len(coverage)

    return (
        f"Данные API: воронка {int(coverage['inFunnelApi'].sum())}/{total}, "
        f"реклама {report.get('adsUniqueNmids', int(coverage['inAdsApi'].sum()))}/{total}, "
        f"поставки {int(coverage['inSuppliesApi'].sum())}/{total}."
    )
