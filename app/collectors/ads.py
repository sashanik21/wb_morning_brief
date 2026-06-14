import os
from datetime import datetime, timedelta

import requests

ADS_PROMOTION_COUNT_URL = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
ADS_FULLSTATS_URL = "https://advert-api.wildberries.ru/adv/v3/fullstats"
ADS_TIMEOUT_SECONDS = 60
ADS_CAMPAIGN_BATCH_SIZE = 50


def _extract_campaign_ids(payload):
    campaign_ids = []

    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("advertId", "campaignId", "id") and item not in (None, ""):
                    campaign_ids.append(str(item))
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)

    return list(dict.fromkeys(campaign_ids))


def _is_stub_status(status_code):
    return status_code in (401, 403, 429)


def _chunked(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def _to_number(value, default=0):
    if value in (None, ""):
        return default

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_percent(numerator, denominator):
    denominator = _to_number(denominator)

    if not denominator:
        return 0

    return round(_to_number(numerator) / denominator * 100, 2)


def _safe_ratio(numerator, denominator):
    denominator = _to_number(denominator)

    if not denominator:
        return 0

    return round(_to_number(numerator) / denominator, 2)


def _request_ads_campaign_ids(token):
    response = requests.get(
        ADS_PROMOTION_COUNT_URL,
        headers={"Authorization": token},
        timeout=ADS_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        print("WB Ads campaigns API error")
        print("STATUS:", response.status_code)
        print("TEXT:", response.text)
        return None, response.status_code

    try:
        payload = response.json()
    except ValueError:
        print("WB Ads campaigns API returned invalid JSON")
        return None, response.status_code

    return _extract_campaign_ids(payload), response.status_code


def _request_ads_fullstats(token, campaign_ids, begin_date, end_date):
    response = requests.get(
        ADS_FULLSTATS_URL,
        headers={"Authorization": token},
        params={
            "ids": ",".join(str(campaign_id) for campaign_id in campaign_ids),
            "beginDate": begin_date,
            "endDate": end_date,
        },
        timeout=ADS_TIMEOUT_SECONDS,
    )

    if response.status_code != 200:
        print("WB Ads API error")
        print("STATUS:", response.status_code)
        print("TEXT:", response.text)
        return None

    try:
        return response.json()
    except ValueError:
        print("WB Ads API returned invalid JSON")
        return None


def _flatten_nm_stats(campaign):
    nm_rows = []

    for day in campaign.get("days") or []:
        for app in day.get("apps") or []:
            nm_rows.extend(app.get("nms") or [])

    return nm_rows


def _campaign_id(campaign):
    return campaign.get("advertId") or campaign.get("campaignId") or campaign.get("id")


def _campaign_name(campaign):
    return (
        campaign.get("advertName")
        or campaign.get("campaignName")
        or campaign.get("name")
        or campaign.get("title")
        or f"Кампания {_campaign_id(campaign) or 'n/a'}"
    )


def _extract_search_queries(campaign):
    query_rows = []
    containers = (
        campaign.get("searchQueries"),
        campaign.get("queries"),
        campaign.get("keywords"),
        campaign.get("searchPhrases"),
    )
    for container in containers:
        if not isinstance(container, list):
            continue
        for item in container:
            if not isinstance(item, dict):
                continue
            query = item.get("query") or item.get("keyword") or item.get("phrase")
            impressions = _to_number(item.get("views") or item.get("impressions"))
            clicks = _to_number(item.get("clicks"))
            spend = _to_number(item.get("sum") or item.get("spend"))
            orders = _to_number(item.get("orders"))
            revenue = _to_number(item.get("sum_price") or item.get("ordersSum"))
            query_rows.append(
                {
                    "query": query,
                    "impressions": int(impressions),
                    "clicks": int(clicks),
                    "ctr": _safe_percent(clicks, impressions),
                    "spend": round(spend, 2),
                    "orders": int(orders),
                    "drr": _safe_percent(spend, revenue),
                }
            )
    return query_rows


def _aggregate_campaign(campaign):
    nm_rows = _flatten_nm_stats(campaign)
    impressions = _to_number(campaign.get("views") or campaign.get("impressions"))
    clicks = _to_number(campaign.get("clicks"))
    spend = _to_number(campaign.get("sum") or campaign.get("spend"))
    orders = _to_number(campaign.get("orders"))
    orders_sum = _to_number(
        campaign.get("sum_price")
        or campaign.get("ordersSum")
        or campaign.get("orderSum")
        or campaign.get("sumPrice")
    )

    if nm_rows:
        impressions = impressions or sum(
            _to_number(row.get("views") or row.get("impressions")) for row in nm_rows
        )
        clicks = clicks or sum(_to_number(row.get("clicks")) for row in nm_rows)
        spend = spend or sum(
            _to_number(row.get("sum") or row.get("spend")) for row in nm_rows
        )
        orders = orders or sum(_to_number(row.get("orders")) for row in nm_rows)
        orders_sum = orders_sum or sum(
            _to_number(row.get("sum_price") or row.get("ordersSum")) for row in nm_rows
        )

    first_nm = nm_rows[0] if nm_rows else {}
    ctr = _to_number(campaign.get("ctr")) or _safe_percent(clicks, impressions)
    cpc = _to_number(campaign.get("cpc")) or _safe_ratio(spend, clicks)
    cpm = _to_number(campaign.get("cpm")) or _safe_ratio(spend * 1000, impressions)
    drr = _safe_percent(spend, orders_sum)

    return {
        "campaignId": _campaign_id(campaign),
        "campaignName": _campaign_name(campaign),
        "nmId": first_nm.get("nm") or first_nm.get("nmId") or campaign.get("nm"),
        "vendorCode": first_nm.get("vendorCode") or campaign.get("vendorCode") or "",
        "title": first_nm.get("name")
        or first_nm.get("title")
        or campaign.get("title")
        or "",
        "impressions": int(impressions),
        "clicks": int(clicks),
        "ctr": round(ctr, 2),
        "cpc": round(cpc, 2),
        "cpm": round(cpm, 2),
        "orders": int(orders),
        "ordersSum": round(orders_sum, 2),
        "spend": round(spend, 2),
        "drr": round(drr, 2),
        "bid": _to_number(campaign.get("bid") or campaign.get("cpmBid")),
        "avgPosition": _to_number(
            campaign.get("avgPosition") or campaign.get("avgAdPosition")
        ),
        "searchQueries": _extract_search_queries(campaign),
    }


def _merge_previous_period(current_rows, previous_rows):
    previous_by_campaign = {row.get("campaignId"): row for row in previous_rows}

    for row in current_rows:
        previous = previous_by_campaign.get(row.get("campaignId"), {})
        for metric in (
            "impressions",
            "clicks",
            "ctr",
            "cpc",
            "cpm",
            "orders",
            "ordersSum",
            "spend",
            "drr",
        ):
            row[f"previous{metric[0].upper()}{metric[1:]}"] = previous.get(metric, 0)

    return current_rows


def _collect_ads_stats_from_api(token, campaign_ids, report_date):
    current_date = report_date.strftime("%Y-%m-%d")
    previous_date = (report_date - timedelta(days=1)).strftime("%Y-%m-%d")
    current_rows = []
    previous_rows = []

    for campaign_id_batch in _chunked(campaign_ids, ADS_CAMPAIGN_BATCH_SIZE):
        current_payload = _request_ads_fullstats(
            token, campaign_id_batch, current_date, current_date
        )
        previous_payload = _request_ads_fullstats(
            token, campaign_id_batch, previous_date, previous_date
        )

        if current_payload is None:
            return None

        current_rows.extend(
            _aggregate_campaign(campaign) for campaign in current_payload
        )

        if previous_payload:
            previous_rows.extend(
                _aggregate_campaign(campaign) for campaign in previous_payload
            )

    for row in current_rows:
        row["date"] = current_date
        row["selectedPeriod"] = current_date
        row["pastPeriod"] = previous_date

    return _merge_previous_period(current_rows, previous_rows)


def collect_ads_stats(report_date=None):
    report_date = report_date or (datetime.now().date() - timedelta(days=1))
    ads_token = os.getenv("WB_ADS_API_TOKEN")
    fallback_token = os.getenv("WB_API_TOKEN_TEST")
    token = ads_token or fallback_token
    token_source = "WB_ADS_API_TOKEN" if ads_token else "WB_API_TOKEN_TEST"

    if token:
        print(f"ADS TOKEN SOURCE: {token_source}")
    else:
        print("ADS TOKEN: not configured")
        print("Ads collector работает в stub mode")
        print("Ads rows: 0")
        return []

    campaign_ids, campaign_status = _request_ads_campaign_ids(token)

    if campaign_ids is None:
        if _is_stub_status(campaign_status):
            print("Ads collector работает в stub mode")
        else:
            print("Ads collector fallback to stub mode")
        print("Ads rows: 0")
        return []

    print("ADS CAMPAIGN IDS SOURCE: api")
    print(f"ADS CAMPAIGNS FOUND: {len(campaign_ids)}")

    if not campaign_ids:
        print("Ads collector работает в stub mode")
        print("Ads rows: 0")
        return []

    ads_rows = _collect_ads_stats_from_api(token, campaign_ids, report_date)

    if ads_rows is None:
        print("Ads collector fallback to stub mode")
        print("Ads rows: 0")
        return []

    print(f"Ads rows: {len(ads_rows)}")
    return ads_rows
