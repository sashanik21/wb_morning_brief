import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import app.config as wb_config
from app.core.date_engine import get_current_period, get_previous_period, to_business_date

ADS_PROMOTION_COUNT_URL = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
ADS_FULLSTATS_URL = "https://advert-api.wildberries.ru/adv/v3/fullstats"
ADS_CAMPAIGN_DETAILS_URL = "https://advert-api.wildberries.ru/api/advert/v2/adverts"

ADS_TIMEOUT_SECONDS = 60
ADS_CAMPAIGN_BATCH_SIZE = 50
ADS_CAMPAIGN_DETAILS_STATUSES = (7, 9, 11)
ADS_CAMPAIGN_DETAILS_TYPES = (4, 5, 6, 7, 8, 9)
ADS_CAMPAIGN_DETAILS_LIMIT = 100
ADS_CAMPAIGN_CACHE_TTL_HOURS = 12
REPORTS_DIR = Path("reports")

LOG_LEVEL = os.getenv("LOG_LEVEL", "summary").strip().lower()

_ADS_API_HAD_429 = False
_ADS_RATE_LIMIT_STATS = {}
_ADS_COLLECTOR_DEADLINE = None


def _is_debug_log():
    return LOG_LEVEL == "debug"


def _debug_log(*args):
    if _is_debug_log():
        print(*args)


def _summary_log(*args):
    print(*args)


def _ads_error_summary(response):
    if response is None:
        return "WB Ads API error: status=n/a"

    title = ""
    request_id = ""
    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if isinstance(payload, dict):
        title = (
            payload.get("title")
            or payload.get("error")
            or payload.get("message")
            or ""
        )
        request_id = (
            payload.get("requestId")
            or payload.get("request_id")
            or payload.get("traceId")
            or payload.get("trace_id")
            or ""
        )

    parts = [f"WB Ads API error: status={response.status_code}"]
    if title:
        parts.append(f"title={title}")
    if request_id:
        parts.append(f"requestId={request_id}")
    return " ".join(parts)

def ads_api_had_429():
    return _ADS_API_HAD_429 or bool(_ADS_RATE_LIMIT_STATS.get("partial"))


def ads_rate_limit_stats():
    return dict(_ADS_RATE_LIMIT_STATS)


def _mark_ads_api_status(status_code):
    global _ADS_API_HAD_429

    if status_code == 429 or (status_code is not None and status_code >= 500):
        _ADS_API_HAD_429 = True
        _ADS_RATE_LIMIT_STATS["partial"] = True


ADS_CAMPAIGN_TYPE_FIELDS = (
    "type",
    "advertType",
    "campaignType",
    "campaign_type",
    "bid_type",
)


def _campaign_type_field(campaign):
    for field in ADS_CAMPAIGN_TYPE_FIELDS:
        value = campaign.get(field)
        if value not in (None, ""):
            return field, value
    return None, None


def _extract_campaign_type(campaign):
    _, value = _campaign_type_field(campaign)
    return value or "unknown"


def _campaign_raw_json(campaign):
    raw_json = campaign.get("raw_json") if isinstance(campaign, dict) else None

    if isinstance(raw_json, str):
        try:
            raw_json = json.loads(raw_json)
        except ValueError:
            raw_json = {"raw_json": raw_json}

    return raw_json if isinstance(raw_json, dict) else campaign


def _campaign_record_id(campaign, raw_json):
    if isinstance(campaign, dict):
        return campaign.get("campaign_id") or _campaign_id(raw_json)
    return _campaign_id(raw_json)


def _ensure_campaign_type_from_raw_json(campaigns):
    for campaign in campaigns or []:
        if not isinstance(campaign, dict):
            continue

        if campaign.get("campaign_type") in (None, ""):
            campaign["campaign_type"] = _extract_campaign_type(
                _campaign_raw_json(campaign)
            )

        if campaign.get("campaign_type") in (None, ""):
            campaign["campaign_type"] = "unknown"

    return campaigns


def _campaign_type_is_unknown(campaign):
    return (
        not isinstance(campaign, dict)
        or campaign.get("campaign_type") in (None, "", "unknown")
    )


def _log_ads_campaign_raw(campaigns):
    for campaign in (campaigns or [])[:10]:
        raw_json = _campaign_raw_json(campaign)
        _debug_log("ADS CAMPAIGN RAW:")
        _debug_log(f"campaign_id: {_campaign_record_id(campaign, raw_json)}")
        _debug_log(f"raw_json: {json.dumps(raw_json, ensure_ascii=False, default=str)}")


def _log_ads_campaign_type_detection(campaigns):
    for campaign in (campaigns or [])[:10]:
        raw_json = _campaign_raw_json(campaign)
        campaign_id = _campaign_record_id(campaign, raw_json)
        source_field, raw_value = _campaign_type_field(raw_json)

        if source_field:
            _debug_log("ADS CAMPAIGN TYPE FOUND:")
            _debug_log(f"campaign_id: {campaign_id}")
            _debug_log(f"source_field: {source_field}")
            _debug_log(f"raw_value: {raw_value}")
            _debug_log(f"resolved_type: {raw_value}")
        else:
            _debug_log("ADS CAMPAIGN TYPE NOT FOUND:")
            _debug_log(f"campaign_id: {campaign_id}")
            _debug_log(f"available_keys: {list(raw_json.keys())}")


def _extract_campaign_records(payload):
    records = []
    seen = set()

    def record_from_dict(value):
        campaign_id = (
            value.get("advertId") or value.get("campaignId") or value.get("id")
        )

        if campaign_id in (None, "") or str(campaign_id) in seen:
            return

        seen.add(str(campaign_id))
        records.append(
            {
                "campaign_id": campaign_id,
                "campaign_name": _campaign_name(value),
                "campaign_status": _extract_status(value),
                "campaign_type": "unknown",
                "raw_json": value,
            }
        )

    def walk(value):
        if isinstance(value, dict):
            record_from_dict(value)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return records


def _campaign_detail_record(value):
    return {
        "campaign_id": _campaign_id(value),
        "campaign_name": _campaign_name(value),
        "campaign_status": _extract_status(value),
        "campaign_type": _extract_campaign_type(value),
        "payment_type": value.get("paymentType") or value.get("payment_type"),
        "placement": value.get("placement"),
        "raw_json": value,
    }


def _extract_campaign_ids(payload):
    return [str(row["campaign_id"]) for row in _extract_campaign_records(payload)]


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


def _ads_collect_time_exceeded():
    return (
        _ADS_COLLECTOR_DEADLINE is not None
        and time.monotonic() >= _ADS_COLLECTOR_DEADLINE
    )


def _ads_sleep(seconds, deadline=None):
    if seconds <= 0 or _ads_collect_time_exceeded():
        return

    deadlines = [
        value for value in (_ADS_COLLECTOR_DEADLINE, deadline) if value is not None
    ]

    if not deadlines:
        time.sleep(seconds)
        return

    remaining = min(deadlines) - time.monotonic()

    if remaining > 0:
        time.sleep(min(seconds, remaining))


def _request_ads_campaign_ids(token):
    response = requests.get(
        ADS_PROMOTION_COUNT_URL,
        headers={"Authorization": token},
        timeout=ADS_TIMEOUT_SECONDS,
    )

    _mark_ads_api_status(response.status_code)

    if response.status_code != 200:
        _summary_log(_ads_error_summary(response))
        _debug_log("TEXT:", response.text)
        return None, response.status_code

    try:
        payload = response.json()
    except ValueError:
        _summary_log("WB Ads campaigns API returned invalid JSON")
        return None, response.status_code

    return _extract_campaign_records(payload), response.status_code


def _extract_campaign_detail_records(payload, requested_ids):
    requested_ids = {
        str(value) for value in requested_ids or [] if value not in (None, "")
    }
    records = {}

    def collect(value):
        if isinstance(value, dict):
            campaign_id = _campaign_id(value)
            if campaign_id not in (None, "") and str(campaign_id) in requested_ids:
                current = records.get(str(campaign_id), {})
                if len(value.keys()) >= len((current.get("raw_json") or {}).keys()):
                    records[str(campaign_id)] = _campaign_detail_record(value)
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(payload)
    return list(records.values())


def _log_ads_campaign_details_request(url, status, campaign_type, limit, offset):
    _debug_log("ADS CAMPAIGN DETAILS REQUEST:")
    _debug_log(f"url: {url}")
    _debug_log(f"status: {status}")
    _debug_log(f"type: {campaign_type}")
    _debug_log(f"limit: {limit}")
    _debug_log(f"offset: {offset}")


def _log_ads_campaign_details_result(rows_loaded, matched_campaign_ids):
    _summary_log("ADS CAMPAIGN DETAILS RESULT:")
    _summary_log(f"rows loaded: {rows_loaded}")
    _summary_log(f"matched campaign ids: {len(matched_campaign_ids or [])}")


def _log_ads_campaign_details(campaigns):
    for campaign in campaigns or []:
        _debug_log("ADS CAMPAIGN DETAILS:")
        _debug_log(f"campaign_id: {campaign.get('campaign_id')}")
        _debug_log(f"campaign_type: {campaign.get('campaign_type') or 'unknown'}")
        _debug_log(f"campaign_name: {campaign.get('campaign_name')}")


def _campaign_detail_items(payload):
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        return []

    for key in ("adverts", "campaigns", "data", "items", "content"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    return []


def _request_ads_campaign_details(token, campaign_ids):
    requested_ids = {
        str(campaign_id)
        for campaign_id in campaign_ids or []
        if campaign_id not in (None, "")
    }

    if not requested_ids:
        _log_ads_campaign_details_request(
            ADS_CAMPAIGN_DETAILS_URL, None, None, ADS_CAMPAIGN_DETAILS_LIMIT, 0
        )
        _log_ads_campaign_details_result(0, [])
        return [], None

    loaded_campaigns = {}
    rows_loaded = 0
    status_code = None

    for status in ADS_CAMPAIGN_DETAILS_STATUSES:
        for campaign_type in ADS_CAMPAIGN_DETAILS_TYPES:
            offset = 0
            while True:
                params = {
                    "status": status,
                    "type": campaign_type,
                    "order": "id",
                    "direction": "asc",
                    "limit": ADS_CAMPAIGN_DETAILS_LIMIT,
                    "offset": offset,
                }

                _log_ads_campaign_details_request(
                    ADS_CAMPAIGN_DETAILS_URL,
                    status,
                    campaign_type,
                    ADS_CAMPAIGN_DETAILS_LIMIT,
                    offset,
                )

                response = requests.get(
                    ADS_CAMPAIGN_DETAILS_URL,
                    headers={"Authorization": token},
                    params=params,
                    timeout=ADS_TIMEOUT_SECONDS,
                )
                status_code = response.status_code
                _mark_ads_api_status(status_code)

                if status_code != 200:
                    _summary_log(_ads_error_summary(response))
                    _debug_log("TEXT:", response.text)
                    break

                try:
                    payload = response.json()
                except ValueError:
                    _summary_log("WB Ads campaign details API returned invalid JSON")
                    break

                items = _campaign_detail_items(payload)
                rows_loaded += len(items)

                for detail in _extract_campaign_detail_records(items, requested_ids):
                    loaded_campaigns[str(detail.get("campaign_id"))] = detail

                if len(items) < ADS_CAMPAIGN_DETAILS_LIMIT:
                    break

                offset += ADS_CAMPAIGN_DETAILS_LIMIT

    campaigns = list(loaded_campaigns.values())
    _ensure_campaign_type_from_raw_json(campaigns)
    _log_ads_campaign_details_result(rows_loaded, sorted(loaded_campaigns.keys()))
    _log_ads_campaign_details(campaigns)

    return campaigns, status_code


def _merge_campaign_details(campaigns, details):
    details_by_id = {
        str(row.get("campaign_id")): row
        for row in details or []
        if row.get("campaign_id") not in (None, "")
    }

    merged = []

    for campaign in campaigns or []:
        campaign_id = str(campaign.get("campaign_id"))
        detail = details_by_id.get(campaign_id)

        if detail:
            merged.append({**campaign, **detail})
        else:
            merged.append({**campaign, "campaign_type": "unknown"})

    return _ensure_campaign_type_from_raw_json(merged)


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _request_ads_fullstats(token, campaign_ids, begin_date, end_date, deadline=None):
    global _ADS_RATE_LIMIT_STATS

    retry_sleep = 20
    _ADS_RATE_LIMIT_STATS["retry_sleep_seconds"] = retry_sleep
    response = None

    for attempt in range(2):
        if _ads_collect_time_exceeded() or (
            deadline is not None and time.monotonic() >= deadline
        ):
            _ADS_RATE_LIMIT_STATS["partial"] = True
            if _ads_collect_time_exceeded():
                _ADS_RATE_LIMIT_STATS["stopped_by_time_limit"] = True
            break

        request_timeout = ADS_TIMEOUT_SECONDS
        deadlines = [
            value for value in (_ADS_COLLECTOR_DEADLINE, deadline) if value is not None
        ]

        if deadlines:
            request_timeout = max(
                1, min(ADS_TIMEOUT_SECONDS, min(deadlines) - time.monotonic())
            )

        response = requests.get(
            ADS_FULLSTATS_URL,
            headers={"Authorization": token},
            params={
                "ids": ",".join(str(campaign_id) for campaign_id in campaign_ids),
                "beginDate": begin_date,
                "endDate": end_date,
            },
            timeout=request_timeout,
        )

        _mark_ads_api_status(response.status_code)

        if response.status_code == 429:
            _ADS_RATE_LIMIT_STATS["429_count"] = (
                _ADS_RATE_LIMIT_STATS.get("429_count", 0) + 1
            )
            _ADS_RATE_LIMIT_STATS["partial"] = True
            break

        if response.status_code >= 500 and attempt == 0:
            _ADS_RATE_LIMIT_STATS["retries_used"] = (
                _ADS_RATE_LIMIT_STATS.get("retries_used", 0) + 1
            )
            _ads_sleep(retry_sleep, deadline=deadline)
            continue

        break

    if response is None or response.status_code != 200:
        _summary_log(_ads_error_summary(response))
        _debug_log("TEXT:", response.text if response is not None else "")
        return None, response.status_code if response is not None else None

    try:
        return response.json(), response.status_code
    except ValueError:
        _summary_log("WB Ads API returned invalid JSON")
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
    if not _is_debug_log():
        return

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
    _debug_log(f"ADS RAW DEBUG DUMP: {path}")


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
        "campaignType": _extract_campaign_type(campaign),
    }


def _enrich_ads_rows_with_campaign_details(rows, campaigns):
    campaigns_by_id = {
        str(campaign.get("campaign_id")): campaign
        for campaign in campaigns or []
        if campaign.get("campaign_id") not in (None, "")
    }

    for row in rows or []:
        campaign_id = str(row.get("campaignId") or row.get("advertId") or "")
        campaign = campaigns_by_id.get(campaign_id)

        if not campaign:
            row["campaignType"] = row.get("campaignType") or "unknown"
            continue

        row["campaignType"] = (
            row.get("campaignType")
            or campaign.get("campaign_type")
            or _extract_campaign_type(campaign.get("raw_json") or campaign)
            or "unknown"
        )
        row["campaignName"] = row.get("campaignName") or campaign.get(
            "campaign_name"
        )
        row["campaignStatus"] = (
            row.get("campaignStatus") or campaign.get("campaign_status")
        )

    return rows


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
            row[f"previous{metric[0].upper()}{metric[1:]}"] = previous.get(metric) if previous else None

    return current_rows


def _append_campaign_rows(target_rows, campaign):
    campaign_rows = []
    nm_rows = _flatten_nm_stats(campaign)

    if nm_rows:
        campaign_rows.extend(
            _aggregate_campaign(campaign, nm_row) for nm_row in nm_rows
        )
    else:
        campaign_rows.append(_aggregate_campaign(campaign))

    target_rows.extend(campaign_rows)
    return campaign_rows


def _log_ads_campaign_result(campaign_id, status, rows):
    _debug_log("ADS CAMPAIGN RESULT:")
    _debug_log(f"campaign_id: {campaign_id}")
    _debug_log(f"status: {status}")
    _debug_log(f"rows: {rows}")


def _log_ads_campaign_time_limit(campaign_id, elapsed):
    _summary_log("ADS CAMPAIGN TIME LIMIT:")
    _summary_log(f"campaign_id: {campaign_id}")
    _summary_log(f"elapsed: {elapsed:.1f}")
    _summary_log("status: timeout")


def _update_campaign_health(seller_id, campaign_id, status, rows=0, error_code=None):
    storage = _storage()

    if (
        not seller_id
        or not storage
        or not hasattr(storage, "update_ads_campaign_stats_status")
    ):
        return

    storage.update_ads_campaign_stats_status(
        seller_id, [campaign_id], status, rows=rows, error_code=error_code
    )


def _log_ads_fullstats_batch_mode(campaign_ids, batches):
    _debug_log("ADS FULLSTATS BATCH MODE:")
    _debug_log(f"campaign ids total: {len(campaign_ids or [])}")
    _debug_log(f"batch size: {ADS_CAMPAIGN_BATCH_SIZE}")
    _debug_log(f"batches: {len(batches or [])}")


def _log_ads_fullstats_batch_request(
    batch_index, batches_count, batch, begin_date, end_date
):
    _debug_log("ADS FULLSTATS BATCH REQUEST:")
    _debug_log(f"batch: {batch_index}/{batches_count}")
    _debug_log(f"campaigns in batch: {len(batch or [])}")
    _debug_log(f"beginDate: {begin_date}")
    _debug_log(f"endDate: {end_date}")


def _log_ads_fullstats_batch_result(batch_index, batches_count, rows, status):
    _debug_log("ADS FULLSTATS BATCH RESULT:")
    _debug_log(f"batch: {batch_index}/{batches_count}")
    _debug_log(f"rows: {rows}")
    _debug_log(f"status: {status}")


def _collect_ads_period_batches(token, campaign_ids, begin_date, end_date, report_date):
    payloads = []
    batches = list(_chunked(campaign_ids or [], ADS_CAMPAIGN_BATCH_SIZE))
    batches_count = len(batches)

    for index, batch in enumerate(batches, start=1):
        if _ads_collect_time_exceeded():
            _ADS_RATE_LIMIT_STATS["partial"] = True
            _ADS_RATE_LIMIT_STATS["stopped_by_time_limit"] = True
            _log_ads_fullstats_batch_result(index, batches_count, 0, "partial")
            break

        _log_ads_fullstats_batch_request(
            index, batches_count, batch, begin_date, end_date
        )
        payload, status_code = _request_ads_fullstats(
            token, batch, begin_date, end_date
        )

        if status_code == 429:
            _ADS_RATE_LIMIT_STATS["partial"] = True
            _log_ads_fullstats_batch_result(index, batches_count, 0, "partial")
            continue

        if payload is None:
            _ADS_RATE_LIMIT_STATS["partial"] = True
            batch_status = "partial" if status_code and status_code >= 500 else "error"
            _log_ads_fullstats_batch_result(index, batches_count, 0, batch_status)
            continue

        rows = len(payload) if isinstance(payload, list) else 0
        payloads.extend(payload if isinstance(payload, list) else [])
        _debug_ads_raw(payload if isinstance(payload, list) else [], report_date)
        _log_ads_fullstats_batch_result(index, batches_count, rows, "success")

        if index < batches_count:
            _ads_sleep(20)

    return payloads


def _collect_ads_stats_from_api(token, campaign_ids, report_date, seller_id=None):
    current_start, current_end = get_current_period(report_date, report_date)
    previous_start, previous_end = get_previous_period(current_start, current_end)
    current_date = current_end.strftime("%Y-%m-%d")
    previous_date = previous_end.strftime("%Y-%m-%d")
    current_rows = []
    previous_rows = []

    campaign_ids = [
        str(campaign_id) for campaign_id in campaign_ids or [] if campaign_id
    ]
    batches = list(_chunked(campaign_ids, ADS_CAMPAIGN_BATCH_SIZE))
    _log_ads_fullstats_batch_mode(campaign_ids, batches)

    if not campaign_ids:
        _ADS_RATE_LIMIT_STATS["campaigns_requested"] = 0
        _ADS_RATE_LIMIT_STATS["campaigns_loaded"] = 0
        _ADS_RATE_LIMIT_STATS["campaigns_attempted"] = 0
        _ADS_RATE_LIMIT_STATS["campaigns_success"] = 0
        _ADS_RATE_LIMIT_STATS["campaigns_partial"] = 0
        _ADS_RATE_LIMIT_STATS["campaigns_failed"] = 0
        _ADS_RATE_LIMIT_STATS["partial_rows_saved"] = 0
        return []

    current_payload = _collect_ads_period_batches(
        token, campaign_ids, current_date, current_date, report_date
    )
    previous_payload = _collect_ads_period_batches(
        token, campaign_ids, previous_date, previous_date, report_date
    )

    loaded_campaign_ids = set()

    for campaign in current_payload:
        campaign_id = str(_campaign_id(campaign) or "")
        if campaign_id:
            loaded_campaign_ids.add(campaign_id)
        _append_campaign_rows(current_rows, campaign)

    for campaign in previous_payload:
        _append_campaign_rows(previous_rows, campaign)

    failed_campaign_ids = set(campaign_ids) - loaded_campaign_ids
    partial_campaign_ids = (
        failed_campaign_ids if _ADS_RATE_LIMIT_STATS.get("partial") else set()
    )
    success_campaign_ids = loaded_campaign_ids - partial_campaign_ids

    for campaign_id in success_campaign_ids:
        _update_campaign_health(seller_id, campaign_id, "success")

    for campaign_id in partial_campaign_ids:
        _update_campaign_health(seller_id, campaign_id, "partial")

    _ADS_RATE_LIMIT_STATS["campaigns_requested"] = len(campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_loaded"] = len(
        {cid for cid in loaded_campaign_ids if cid}
    )
    _ADS_RATE_LIMIT_STATS["campaigns_attempted"] = len(campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_success"] = len(success_campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_partial"] = len(partial_campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_failed"] = (
        0 if _ADS_RATE_LIMIT_STATS.get("partial") else len(failed_campaign_ids)
    )
    _ADS_RATE_LIMIT_STATS["partial"] = bool(_ADS_RATE_LIMIT_STATS.get("partial"))
    _ADS_RATE_LIMIT_STATS["partial_rows_saved"] = len(current_rows)

    for row in current_rows:
        row["business_date"] = to_business_date({"campaign_date": current_date})
        row["campaign_date"] = row["business_date"]
        row["date"] = row["business_date"]
        row["selectedPeriod"] = row["business_date"]
        row["pastPeriod"] = previous_date

    return _merge_previous_period(current_rows, previous_rows)


def _storage():
    try:
        from app.storage import supabase_storage as storage
    except Exception as error:
        _summary_log(f"WARNING: Ads Supabase storage unavailable: {error}")
        return None

    return storage


def _env_bool(name, default=False):
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {"1", "true", "yes", "y", "да"}


def _parse_datetime(value):
    if not value:
        return None

    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)

    return parsed


def _campaign_cache_age(campaigns):
    timestamps = [_parse_datetime(row.get("last_seen_at")) for row in campaigns or []]
    timestamps = [ts for ts in timestamps if ts]

    if not timestamps:
        return None

    newest = max(timestamps)
    return datetime.utcnow() - newest


def _campaign_cache_is_fresh(campaigns):
    age = _campaign_cache_age(campaigns)

    return (
        bool(campaigns)
        and age is not None
        and age <= timedelta(hours=ADS_CAMPAIGN_CACHE_TTL_HOURS)
    )


def _campaign_ids_from_records(records):
    return [
        str(row.get("campaign_id"))
        for row in records or []
        if row.get("campaign_id") not in (None, "")
    ]


def _campaign_cache_log(source, campaigns, force_refresh):
    age = _campaign_cache_age(campaigns)
    age_text = "n/a" if age is None else str(age).split(".")[0]

    _summary_log("ADS CAMPAIGN CACHE:")
    _summary_log(f"source: {source}")
    _summary_log(f"campaigns: {len(campaigns or [])}")
    _summary_log(f"cache age: {age_text}")
    _summary_log(f"force refresh: {str(bool(force_refresh)).lower()}")


def _load_campaigns(token, seller_id):
    storage = _storage()
    force_refresh = _env_bool("WB_ADS_FORCE_REFRESH_CAMPAIGNS", False)
    cached_campaigns = []

    if storage and hasattr(storage, "get_ads_campaigns_cache"):
        cached_campaigns = storage.get_ads_campaigns_cache(seller_id)

    if not force_refresh and _campaign_cache_is_fresh(cached_campaigns):
        _ensure_campaign_type_from_raw_json(cached_campaigns)

        if any(_campaign_type_is_unknown(campaign) for campaign in cached_campaigns):
            campaign_ids = _campaign_ids_from_records(cached_campaigns)
            detail_campaigns, _ = _request_ads_campaign_details(token, campaign_ids)
            cached_campaigns = _merge_campaign_details(
                cached_campaigns, detail_campaigns
            )
            if storage and hasattr(storage, "save_ads_campaigns_cache"):
                storage.save_ads_campaigns_cache(seller_id, cached_campaigns)
                cached_campaigns = storage.get_ads_campaigns_cache(seller_id)

            _ensure_campaign_type_from_raw_json(cached_campaigns)

        _log_ads_campaign_raw(cached_campaigns)
        _log_ads_campaign_type_detection(cached_campaigns)
        _campaign_cache_log("cache", cached_campaigns, force_refresh)

        return cached_campaigns, 200

    api_campaigns, campaign_status = _request_ads_campaign_ids(token)

    if api_campaigns is None:
        _campaign_cache_log("api", cached_campaigns, force_refresh)
        return None, campaign_status

    campaign_ids = _campaign_ids_from_records(api_campaigns)
    detail_campaigns, _ = _request_ads_campaign_details(token, campaign_ids)
    api_campaigns = _merge_campaign_details(api_campaigns, detail_campaigns)
    _log_ads_campaign_raw(api_campaigns)
    _log_ads_campaign_type_detection(api_campaigns)

    if storage and hasattr(storage, "save_ads_campaigns_cache"):
        storage.save_ads_campaigns_cache(seller_id, api_campaigns)
        cached_campaigns = storage.get_ads_campaigns_cache(seller_id)

    campaigns = _ensure_campaign_type_from_raw_json(cached_campaigns or api_campaigns)
    _campaign_cache_log("api", campaigns, force_refresh)

    return campaigns, campaign_status


def _bid_kopecks_to_rubles(value):
    if value in (None, ""):
        return None

    try:
        return round(float(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def _extract_ads_bid_history_rows(
    campaigns, report_date, seller_id=None, seller_name=None
):
    rows = []

    for campaign in campaigns or []:
        if not isinstance(campaign, dict):
            continue

        raw_json = _campaign_raw_json(campaign)
        campaign_id = _campaign_record_id(campaign, raw_json)

        if campaign_id in (None, ""):
            continue

        settings = (
            raw_json.get("settings")
            if isinstance(raw_json.get("settings"), dict)
            else {}
        )
        timestamps = (
            raw_json.get("timestamps")
            if isinstance(raw_json.get("timestamps"), dict)
            else {}
        )

        base = {
            "seller_id": seller_id,
            "seller_name": seller_name or os.getenv("SELLER_NAME"),
            "campaign_id": campaign_id,
            "report_date": to_business_date({"campaign_date": report_date}),
            "bid_type": raw_json.get("bid_type") or raw_json.get("bidType"),
            "payment_type": settings.get("payment_type")
            or settings.get("paymentType")
            or raw_json.get("payment_type")
            or raw_json.get("paymentType"),
            "campaign_status": _extract_status(raw_json),
            "campaign_updated_at": timestamps.get("updated")
            or raw_json.get("updatedAt"),
        }

        nm_settings = raw_json.get("nm_settings") or raw_json.get("nmSettings")

        if not isinstance(nm_settings, list) or not nm_settings:
            rows.append(
                {
                    **base,
                    "nm_id": None,
                    "search_bid": None,
                    "recommendations_bid": None,
                }
            )
            continue

        for item in nm_settings:
            if not isinstance(item, dict):
                continue

            bids = (
                item.get("bids_kopecks")
                if isinstance(item.get("bids_kopecks"), dict)
                else {}
            )

            rows.append(
                {
                    **base,
                    "nm_id": item.get("nm_id") or item.get("nmId"),
                    "search_bid": _bid_kopecks_to_rubles(bids.get("search")),
                    "recommendations_bid": _bid_kopecks_to_rubles(
                        bids.get("recommendations")
                    ),
                }
            )

    return rows


def _save_ads_bid_history(campaigns, report_date, seller_id=None, seller_name=None):
    bid_rows = _extract_ads_bid_history_rows(
        campaigns, report_date, seller_id=seller_id, seller_name=seller_name
    )
    _summary_log(f"ads bids collected: {len(bid_rows)}")

    storage = _storage()
    changed = 0

    if storage and hasattr(storage, "save_ads_bid_history"):
        storage.save_ads_bid_history(
            bid_rows, seller_id=seller_id, seller_name=seller_name
        )

    if storage and hasattr(storage, "enrich_ads_bid_history_changes"):
        enriched = storage.enrich_ads_bid_history_changes(
            bid_rows, seller_id=seller_id
        )
        changed = sum(
            1
            for row in enriched or []
            if row.get("search_bid_delta") not in (None, "", 0)
            or row.get("recommendations_bid_delta") not in (None, "", 0)
        )

    _summary_log(f"ads bid changes found: {changed}")


def _is_active_campaign(campaign):
    status = str(campaign.get("campaign_status") or "").strip().lower()
    return status in {"active", "running", "enabled", "9", "11", "активна", "активная"}


def _campaign_nm_ids(campaign):
    nm_ids = set()

    def collect(value):
        if isinstance(value, dict):
            for key in ("nmId", "nm_id", "nm", "nmid"):
                if value.get(key) not in (None, ""):
                    nm_ids.add(str(value.get(key)))
            for item in value.values():
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(campaign.get("raw_json") or campaign)
    return nm_ids


def _is_repeated_error_cooldown(campaign, now=None):
    if _to_number(campaign.get("consecutive_errors")) < 3:
        return False

    last_stats = _parse_datetime(campaign.get("last_stats_at"))

    if last_stats is None:
        return False

    return (now or datetime.utcnow()) - last_stats < timedelta(hours=6)


def _select_staged_campaigns(campaigns, top_drop_nm_ids=None, oos_nm_ids=None):
    max_per_run = max(
        ADS_CAMPAIGN_BATCH_SIZE,
        _env_int("WB_ADS_MAX_CAMPAIGNS_PER_RUN", ADS_CAMPAIGN_BATCH_SIZE),
    )
    top_drop_nm_ids = {
        str(value) for value in top_drop_nm_ids or [] if value not in (None, "")
    }
    oos_nm_ids = {str(value) for value in oos_nm_ids or [] if value not in (None, "")}
    now = datetime.utcnow()

    eligible_campaigns = []
    cooldown_skipped = 0

    for campaign in campaigns or []:
        if _is_repeated_error_cooldown(campaign, now=now):
            cooldown_skipped += 1
            _debug_log("ADS CAMPAIGN SKIPPED:")
            _debug_log(f"campaign_id: {campaign.get('campaign_id')}")
            _debug_log("reason: repeated errors cooldown")
            continue

        eligible_campaigns.append(campaign)

    def sort_key(row):
        last_stats = _parse_datetime(row.get("last_stats_at"))
        never_updated_rank = 0 if last_stats is None else 1
        no_stats_rank = 0 if _to_number(row.get("last_stats_rows")) <= 0 else 1
        nm_ids = _campaign_nm_ids(row)
        top_drop_rank = 0 if top_drop_nm_ids & nm_ids else 1
        oos_rank = 0 if oos_nm_ids & nm_ids else 1

        return (
            top_drop_rank,
            oos_rank,
            no_stats_rank,
            never_updated_rank,
            last_stats or datetime.min,
            0 if _is_active_campaign(row) else 1,
            str(row.get("campaign_id")),
        )

    selected = sorted(eligible_campaigns, key=sort_key)[:max_per_run]
    skipped = max(0, len(campaigns or []) - len(selected))

    _summary_log("ADS PRIORITY QUEUE:")
    _summary_log(f"campaigns total: {len(campaigns or [])}")
    _summary_log(f"campaigns selected: {len(selected)}")
    _summary_log(f"campaigns skipped: {skipped}")
    _summary_log(
        "selection reason: TOP drops, OOS forecast, no stats, oldest stats update, then active status"
    )

    if cooldown_skipped:
        _summary_log(f"campaigns skipped by repeated errors cooldown: {cooldown_skipped}")

    selected_ids = {str(row.get("campaign_id")) for row in selected}
    queued_for_future = 0

    for campaign in eligible_campaigns:
        campaign_id = str(campaign.get("campaign_id"))
        if campaign_id and campaign_id not in selected_ids:
            queued_for_future += 1
            _debug_log("ADS CAMPAIGN NEXT RUN:")
            _debug_log(f"campaign_id: {campaign_id}")
            _debug_log("reason: campaign remains queued for a future run")

    if queued_for_future:
        _summary_log(f"campaigns queued for future run: {queued_for_future}")

    return selected


def _coverage_confidence(processed, total):
    if not total:
        return "HIGH"

    coverage = processed / total

    if coverage < 0.8:
        return "LOW"

    if coverage <= 0.95:
        return "MEDIUM"

    return "HIGH"


def collect_ads_stats(
    report_date=None,
    seller_id=None,
    seller_name=None,
    top_drop_nm_ids=None,
    oos_nm_ids=None,
):
    global _ADS_API_HAD_429, _ADS_RATE_LIMIT_STATS, _ADS_COLLECTOR_DEADLINE

    _ADS_API_HAD_429 = False
    _ADS_RATE_LIMIT_STATS = {
        "429_count": 0,
        "retries_used": 0,
        "partial": False,
        "campaigns_requested": 0,
        "campaigns_loaded": 0,
    }

    max_collect_seconds = max(1, _env_int("WB_ADS_MAX_COLLECT_SECONDS", 60))
    collect_started_at = time.monotonic()
    _ADS_COLLECTOR_DEADLINE = collect_started_at + max_collect_seconds
    _ADS_RATE_LIMIT_STATS["max_collect_seconds"] = max_collect_seconds
    _ADS_RATE_LIMIT_STATS["stopped_by_time_limit"] = False
    _ADS_RATE_LIMIT_STATS["partial_rows_saved"] = 0

    report_date = report_date or (datetime.now().date() - timedelta(days=1))
    token_source = wb_config.CURRENT_WB_TOKEN_SECRET_NAME
    token = wb_config.WB_API_TOKEN or os.getenv(token_source)

    if token:
        _summary_log(f"ADS TOKEN SOURCE: {token_source}")
    else:
        _summary_log("ADS TOKEN: not configured")
        _summary_log("Ads collector работает в stub mode")
        _summary_log("Ads rows: 0")
        return []

    seller_id = (
        seller_id or os.getenv("SELLER_ID") or os.getenv("WB_SELLER_ID") or "default"
    )
    campaigns, campaign_status = _load_campaigns(token, seller_id)

    if campaigns is None:
        if _is_stub_status(campaign_status):
            _summary_log("Ads collector работает в stub mode")
        else:
            _summary_log("Ads collector fallback to stub mode")
        _summary_log("Ads rows: 0")
        return []

    _summary_log(f"ADS CAMPAIGNS FOUND: {len(campaigns)}")
    _save_ads_bid_history(
        campaigns, report_date, seller_id=seller_id, seller_name=seller_name
    )

    if not campaigns:
        _summary_log("Ads collector работает в stub mode")
        _summary_log("Ads rows: 0")
        return []

    selected_campaigns = _select_staged_campaigns(
        campaigns, top_drop_nm_ids=top_drop_nm_ids, oos_nm_ids=oos_nm_ids
    )
    campaign_ids = _campaign_ids_from_records(selected_campaigns)
    _ADS_RATE_LIMIT_STATS["campaigns_total"] = len(campaigns)
    _ADS_RATE_LIMIT_STATS["campaigns_selected"] = len(campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_skipped"] = max(
        0, len(campaigns) - len(campaign_ids)
    )

    if _ADS_RATE_LIMIT_STATS["campaigns_skipped"]:
        _ADS_RATE_LIMIT_STATS["partial"] = True

    ads_rows = _collect_ads_stats_from_api(
        token, campaign_ids, report_date, seller_id=seller_id
    )

    if ads_rows is None:
        ads_rows = []

    _enrich_ads_rows_with_campaign_details(ads_rows, selected_campaigns)

    unique_nmids = {
        row.get("nmId") for row in ads_rows if row.get("nmId") not in (None, "")
    }
    row_campaign_ids = {
        row.get("campaignId") or row.get("advertId")
        for row in ads_rows
        if row.get("campaignId") or row.get("advertId")
    }

    _summary_log("ADS RATE LIMIT:")
    _summary_log(f"429 count: {_ADS_RATE_LIMIT_STATS.get('429_count', 0)}")
    _summary_log(f"retries used: {_ADS_RATE_LIMIT_STATS.get('retries_used', 0)}")
    _summary_log(f"partial: {str(bool(_ADS_RATE_LIMIT_STATS.get('partial'))).lower()}")
    _summary_log(
        f"campaigns requested: {_ADS_RATE_LIMIT_STATS.get('campaigns_requested') or len(campaign_ids)}"
    )
    _summary_log(
        f"campaigns loaded: {_ADS_RATE_LIMIT_STATS.get('campaigns_loaded') or len(row_campaign_ids)}"
    )

    _summary_log("ADS COLLECTION SUMMARY:")
    _summary_log(
        f"campaigns attempted: {_ADS_RATE_LIMIT_STATS.get('campaigns_attempted', len(campaign_ids))}"
    )
    _summary_log(f"campaigns success: {_ADS_RATE_LIMIT_STATS.get('campaigns_success', 0)}")
    _summary_log(f"campaigns partial: {_ADS_RATE_LIMIT_STATS.get('campaigns_partial', 0)}")
    _summary_log(f"campaigns failed: {_ADS_RATE_LIMIT_STATS.get('campaigns_failed', 0)}")
    _summary_log(f"ads rows total: {len(ads_rows)}")

    elapsed = round(time.monotonic() - collect_started_at, 2)
    _ADS_RATE_LIMIT_STATS["elapsed_seconds"] = elapsed

    _summary_log("ADS COLLECTOR TIME LIMIT:")
    _summary_log(f"max seconds: {max_collect_seconds}")
    _summary_log(f"elapsed: {elapsed}")
    _summary_log(
        "stopped by limit: "
        f"{str(bool(_ADS_RATE_LIMIT_STATS.get('stopped_by_time_limit'))).lower()}"
    )
    _summary_log(
        f"partial rows saved: {_ADS_RATE_LIMIT_STATS.get('partial_rows_saved', len(ads_rows))}"
    )

    _summary_log("ADS COVERAGE:")
    _summary_log(f"campaigns: {len(campaign_ids) or len(row_campaign_ids)}")
    _summary_log(f"ads rows: {len(ads_rows)}")
    _summary_log(f"unique nmIds: {len(unique_nmids)}")

    total_campaigns = len(campaigns) or len(row_campaign_ids)
    processed_campaigns = _ADS_RATE_LIMIT_STATS.get("campaigns_loaded") or len(
        row_campaign_ids
    )
    confidence = _coverage_confidence(processed_campaigns, total_campaigns)
    _ADS_RATE_LIMIT_STATS["adsCoverageConfidence"] = confidence

    _summary_log(f"coverage confidence: {confidence}")
    _summary_log(f"Ads rows: {len(ads_rows)}")

    storage = _storage()

    if storage and hasattr(storage, "update_ads_campaign_stats_status"):
        processed_ids = {
            str(row.get("campaignId") or row.get("advertId"))
            for row in ads_rows
            if row.get("campaignId") or row.get("advertId")
        }
        failed_ids = set(campaign_ids) - processed_ids

        if processed_ids:
            storage.update_ads_campaign_stats_status(
                seller_id, processed_ids, "success"
            )

        if failed_ids:
            status = "partial" if _ADS_RATE_LIMIT_STATS.get("partial") else "error"
            storage.update_ads_campaign_stats_status(seller_id, failed_ids, status)

    return ads_rows
