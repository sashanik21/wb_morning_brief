import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

ADS_PROMOTION_COUNT_URL = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
ADS_FULLSTATS_URL = "https://advert-api.wildberries.ru/adv/v3/fullstats"
ADS_TIMEOUT_SECONDS = 60
ADS_CAMPAIGN_BATCH_SIZE = 20
REPORTS_DIR = Path("reports")
_ADS_API_HAD_429 = False
_ADS_RATE_LIMIT_STATS = {}


def ads_api_had_429():
    return _ADS_API_HAD_429


def ads_rate_limit_stats():
    return dict(_ADS_RATE_LIMIT_STATS)


def _mark_ads_api_status(status_code):
    global _ADS_API_HAD_429
    if status_code == 429:
        _ADS_API_HAD_429 = True


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

    _mark_ads_api_status(response.status_code)

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


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _request_ads_fullstats(token, campaign_ids, begin_date, end_date):
    global _ADS_RATE_LIMIT_STATS
    retry_count = _env_int("WB_ADS_RETRY_COUNT", 3)
    retry_sleep = _env_int("WB_ADS_RETRY_SLEEP_SECONDS", 20)
    response = None

    for attempt in range(retry_count + 1):
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

        _mark_ads_api_status(response.status_code)
        if response.status_code == 429:
            _ADS_RATE_LIMIT_STATS["429_count"] = (
                _ADS_RATE_LIMIT_STATS.get("429_count", 0) + 1
            )
            if attempt < retry_count:
                _ADS_RATE_LIMIT_STATS["retries_used"] = (
                    _ADS_RATE_LIMIT_STATS.get("retries_used", 0) + 1
                )
                time.sleep(retry_sleep * (attempt + 1))
                continue
        break

    if response is None or response.status_code != 200:
        print("WB Ads API error")
        print("STATUS:", response.status_code if response is not None else "n/a")
        print("TEXT:", response.text if response is not None else "")
        return None, response.status_code if response is not None else None

    try:
        return response.json(), response.status_code
    except ValueError:
        print("WB Ads API returned invalid JSON")
        return None, response.status_code


def _flatten_nm_stats(campaign):
    nm_rows = []

    def append_nm_rows(container):
        for item in container or []:
            if isinstance(item, dict):
                nm_rows.append(item)
            elif item not in (None, ""):
                nm_rows.append({"nmId": item})

    append_nm_rows(campaign.get("nms"))
    append_nm_rows(campaign.get("nmIds"))
    for advert_item in campaign.get("advertItems") or campaign.get("items") or []:
        if isinstance(advert_item, dict):
            append_nm_rows(advert_item.get("nms"))
            append_nm_rows(advert_item.get("nmIds"))
            if advert_item.get("nm") or advert_item.get("nmId"):
                nm_rows.append(advert_item)
    for day in campaign.get("days") or []:
        for app in day.get("apps") or []:
            append_nm_rows(app.get("nms"))

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


def _extract_subject(campaign):
    return (
        campaign.get("subjectName")
        or campaign.get("subject")
        or campaign.get("object")
        or ""
    )


def _extract_status(campaign):
    return (
        campaign.get("status")
        or campaign.get("state")
        or campaign.get("campaignStatus")
        or ""
    )


def _debug_ads_raw(campaigns, report_date):
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"debug_ads_raw_{report_date.strftime('%Y_%m_%d')}.json"
    payload = []
    for campaign in campaigns or []:
        nm_rows = _flatten_nm_stats(campaign)
        payload.append(
            {
                "campaignId": _campaign_id(campaign),
                "campaignName": _campaign_name(campaign),
                "advertId": campaign.get("advertId") or _campaign_id(campaign),
                "nmIds": [
                    row.get("nm") or row.get("nmId")
                    for row in nm_rows
                    if isinstance(row, dict)
                ],
                "subject": _extract_subject(campaign),
                "searchText": ", ".join(
                    q.get("query") or "" for q in _extract_search_queries(campaign)
                ),
                "status": _extract_status(campaign),
                "raw": campaign,
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ADS RAW DEBUG DUMP: {path}")


def _aggregate_campaign(campaign, nm_row=None):
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

    first_nm = nm_row or (nm_rows[0] if nm_rows else {})
    if nm_row:
        impressions = _to_number(
            nm_row.get("views") or nm_row.get("impressions"), impressions
        )
        clicks = _to_number(nm_row.get("clicks"), clicks)
        spend = _to_number(nm_row.get("sum") or nm_row.get("spend"), spend)
        orders = _to_number(nm_row.get("orders"), orders)
        orders_sum = _to_number(
            nm_row.get("sum_price") or nm_row.get("ordersSum"), orders_sum
        )
    ctr = _to_number(campaign.get("ctr")) or _safe_percent(clicks, impressions)
    cpc = _to_number(campaign.get("cpc")) or _safe_ratio(spend, clicks)
    cpm = _to_number(campaign.get("cpm")) or _safe_ratio(spend * 1000, impressions)
    drr = _safe_percent(spend, orders_sum)

    return {
        "campaignId": _campaign_id(campaign),
        "advertId": campaign.get("advertId") or _campaign_id(campaign),
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
        "subject": _extract_subject(campaign),
        "campaignStatus": _extract_status(campaign),
    }


def _merge_previous_period(current_rows, previous_rows):
    previous_by_campaign_nm = {
        (row.get("campaignId"), row.get("nmId")): row for row in previous_rows
    }
    previous_by_campaign = {row.get("campaignId"): row for row in previous_rows}

    for row in current_rows:
        previous = previous_by_campaign_nm.get(
            (row.get("campaignId"), row.get("nmId"))
        ) or previous_by_campaign.get(row.get("campaignId"), {})
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

    batch_size = max(1, _env_int("WB_ADS_BATCH_SIZE", ADS_CAMPAIGN_BATCH_SIZE))
    request_sleep = _env_int("WB_ADS_REQUEST_SLEEP_SECONDS", 2)
    loaded_campaign_ids = set()
    global _ADS_RATE_LIMIT_STATS
    for campaign_id_batch in _chunked(campaign_ids, batch_size):
        current_payload, current_status = _request_ads_fullstats(
            token, campaign_id_batch, current_date, current_date
        )
        time.sleep(request_sleep)
        previous_payload, _ = _request_ads_fullstats(
            token, campaign_id_batch, previous_date, previous_date
        )
        time.sleep(request_sleep)

        if current_payload is None:
            if current_status == 429 and len(campaign_id_batch) > 1:
                for single_campaign_id in campaign_id_batch:
                    single_payload, single_status = _request_ads_fullstats(
                        token, [single_campaign_id], current_date, current_date
                    )
                    time.sleep(request_sleep)
                    if single_payload is None:
                        if single_status == 429:
                            _ADS_RATE_LIMIT_STATS["partial"] = True
                        continue
                    for campaign in single_payload:
                        loaded_campaign_ids.add(
                            str(_campaign_id(campaign) or single_campaign_id)
                        )
                        nm_rows = _flatten_nm_stats(campaign)
                        if nm_rows:
                            current_rows.extend(
                                _aggregate_campaign(campaign, nm_row)
                                for nm_row in nm_rows
                            )
                        else:
                            current_rows.append(_aggregate_campaign(campaign))
                continue
            _ADS_RATE_LIMIT_STATS["partial"] = True
            continue

        _debug_ads_raw(current_payload, report_date)
        for campaign in current_payload:
            loaded_campaign_ids.add(str(_campaign_id(campaign) or ""))
            nm_rows = _flatten_nm_stats(campaign)
            if nm_rows:
                current_rows.extend(
                    _aggregate_campaign(campaign, nm_row) for nm_row in nm_rows
                )
            else:
                current_rows.append(_aggregate_campaign(campaign))

        if previous_payload:
            for campaign in previous_payload:
                nm_rows = _flatten_nm_stats(campaign)
                if nm_rows:
                    previous_rows.extend(
                        _aggregate_campaign(campaign, nm_row) for nm_row in nm_rows
                    )
                else:
                    previous_rows.append(_aggregate_campaign(campaign))

    _ADS_RATE_LIMIT_STATS["campaigns_requested"] = len(campaign_ids or [])
    _ADS_RATE_LIMIT_STATS["campaigns_loaded"] = len(
        {cid for cid in loaded_campaign_ids if cid}
    )
    _ADS_RATE_LIMIT_STATS["partial"] = bool(_ADS_RATE_LIMIT_STATS.get("partial"))

    for row in current_rows:
        row["date"] = current_date
        row["selectedPeriod"] = current_date
        row["pastPeriod"] = previous_date

    return _merge_previous_period(current_rows, previous_rows)


def collect_ads_stats(report_date=None):
    global _ADS_API_HAD_429, _ADS_RATE_LIMIT_STATS
    _ADS_API_HAD_429 = False
    _ADS_RATE_LIMIT_STATS = {
        "429_count": 0,
        "retries_used": 0,
        "partial": False,
        "campaigns_requested": 0,
        "campaigns_loaded": 0,
    }
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
        ads_rows = []

    unique_nmids = {
        row.get("nmId") for row in ads_rows if row.get("nmId") not in (None, "")
    }
    row_campaign_ids = {
        row.get("campaignId") or row.get("advertId")
        for row in ads_rows
        if row.get("campaignId") or row.get("advertId")
    }
    print("ADS RATE LIMIT:")
    print(f"429 count: {_ADS_RATE_LIMIT_STATS.get('429_count', 0)}")
    print(f"retries used: {_ADS_RATE_LIMIT_STATS.get('retries_used', 0)}")
    print(f"partial: {str(bool(_ADS_RATE_LIMIT_STATS.get('partial'))).lower()}")
    print(
        f"campaigns requested: {_ADS_RATE_LIMIT_STATS.get('campaigns_requested') or len(campaign_ids)}"
    )
    print(
        f"campaigns loaded: {_ADS_RATE_LIMIT_STATS.get('campaigns_loaded') or len(row_campaign_ids)}"
    )
    print("ADS COVERAGE:")
    print(f"campaigns: {len(campaign_ids) or len(row_campaign_ids)}")
    print(f"ads rows: {len(ads_rows)}")
    print(f"unique nmIds: {len(unique_nmids)}")
    print(f"Ads rows: {len(ads_rows)}")
    return ads_rows
