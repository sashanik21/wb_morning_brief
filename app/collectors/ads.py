import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ADS_PROMOTION_COUNT_URL = "https://advert-api.wildberries.ru/adv/v1/promotion/count"
ADS_FULLSTATS_URL = "https://advert-api.wildberries.ru/adv/v3/fullstats"
ADS_TIMEOUT_SECONDS = 60
ADS_CAMPAIGN_BATCH_SIZE = 20
ADS_CAMPAIGN_CACHE_TTL_HOURS = 12
REPORTS_DIR = Path("reports")
_ADS_API_HAD_429 = False
_ADS_RATE_LIMIT_STATS = {}
_ADS_COLLECTOR_DEADLINE = None


def ads_api_had_429():
    return _ADS_API_HAD_429 or bool(_ADS_RATE_LIMIT_STATS.get("partial"))


def ads_rate_limit_stats():
    return dict(_ADS_RATE_LIMIT_STATS)


def _mark_ads_api_status(status_code):
    global _ADS_API_HAD_429
    if status_code == 429 or (status_code is not None and status_code >= 500):
        _ADS_API_HAD_429 = True
        _ADS_RATE_LIMIT_STATS["partial"] = True


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
                "campaign_type": value.get("type")
                or value.get("campaignType")
                or value.get("advertType")
                or "",
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

    return _extract_campaign_records(payload), response.status_code


def _env_int(name, default):
    try:
        return int(os.getenv(name, default))
    except (TypeError, ValueError):
        return default


def _request_ads_fullstats(token, campaign_ids, begin_date, end_date, deadline=None):
    global _ADS_RATE_LIMIT_STATS
    retry_count = _env_int("WB_ADS_RETRY_COUNT", 0)
    retry_sleep = _env_int("WB_ADS_RETRY_SLEEP_SECONDS", 0)
    _ADS_RATE_LIMIT_STATS["retry_sleep_seconds"] = retry_sleep
    response = None

    for attempt in range(retry_count + 1):
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
        "campaignType": campaign.get("type")
        or campaign.get("campaignType")
        or campaign.get("advertType")
        or "",
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
    print("ADS CAMPAIGN RESULT:")
    print(f"campaign_id: {campaign_id}")
    print(f"status: {status}")
    print(f"rows: {rows}")


def _log_ads_campaign_time_limit(campaign_id, elapsed):
    print("ADS CAMPAIGN TIME LIMIT:")
    print(f"campaign_id: {campaign_id}")
    print(f"elapsed: {elapsed:.1f}")
    print("status: timeout")


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


def _collect_ads_stats_from_api(token, campaign_ids, report_date, seller_id=None):
    current_date = report_date.strftime("%Y-%m-%d")
    previous_date = (report_date - timedelta(days=1)).strftime("%Y-%m-%d")
    current_rows = []
    previous_rows = []

    request_sleep = _env_int("WB_ADS_REQUEST_SLEEP_SECONDS", 3)
    loaded_campaign_ids = set()
    attempted_campaign_ids = set()
    success_campaign_ids = set()
    partial_campaign_ids = set()
    failed_campaign_ids = set()
    global _ADS_RATE_LIMIT_STATS
    max_campaign_seconds = max(1, _env_int("WB_ADS_MAX_SECONDS_PER_CAMPAIGN", 8))
    for campaign_id in campaign_ids or []:
        campaign_started_at = time.monotonic()
        campaign_deadline = campaign_started_at + max_campaign_seconds
        attempted_campaign_ids.add(str(campaign_id))
        if _ads_collect_time_exceeded():
            _ADS_RATE_LIMIT_STATS["partial"] = True
            _ADS_RATE_LIMIT_STATS["stopped_by_time_limit"] = True
            partial_campaign_ids.add(str(campaign_id))
            print("ADS CAMPAIGN NEXT RUN:")
            print(f"campaign_id: {campaign_id}")
            print("reason: collector time limit reached")
            _log_ads_campaign_result(campaign_id, "timeout", 0)
            _update_campaign_health(
                seller_id, campaign_id, "timeout", error_code="time_limit"
            )
            continue
        previous_status = None
        current_payload, current_status = _request_ads_fullstats(
            token, [campaign_id], current_date, current_date, deadline=campaign_deadline
        )
        if current_status == 429:
            _ADS_RATE_LIMIT_STATS["partial"] = True
            partial_campaign_ids.add(str(campaign_id))
            _log_ads_campaign_result(campaign_id, "partial", 0)
            _update_campaign_health(
                seller_id, campaign_id, "partial", error_code="429"
            )
            continue
        _ads_sleep(request_sleep, deadline=campaign_deadline)
        if time.monotonic() >= campaign_deadline:
            _ADS_RATE_LIMIT_STATS["partial"] = True
            partial_campaign_ids.add(str(campaign_id))
            elapsed = time.monotonic() - campaign_started_at
            _log_ads_campaign_time_limit(campaign_id, elapsed)
            _log_ads_campaign_result(campaign_id, "timeout", 0)
            _update_campaign_health(
                seller_id, campaign_id, "timeout", error_code="timeout"
            )
            continue
        if _ads_collect_time_exceeded():
            _ADS_RATE_LIMIT_STATS["partial"] = True
            _ADS_RATE_LIMIT_STATS["stopped_by_time_limit"] = True
            previous_payload = None
        else:
            previous_payload, previous_status = _request_ads_fullstats(
                token,
                [campaign_id],
                previous_date,
                previous_date,
                deadline=campaign_deadline,
            )
        _ads_sleep(request_sleep, deadline=campaign_deadline)

        if current_payload is None:
            _ADS_RATE_LIMIT_STATS["partial"] = True
            if current_status == 429:
                partial_campaign_ids.add(str(campaign_id))
                _log_ads_campaign_result(campaign_id, "partial", 0)
                _update_campaign_health(
                    seller_id, campaign_id, "partial", error_code="429"
                )
            else:
                failed_campaign_ids.add(str(campaign_id))
                _log_ads_campaign_result(campaign_id, "error", 0)
                _update_campaign_health(
                    seller_id,
                    campaign_id,
                    "error",
                    error_code=str(current_status or "unknown"),
                )
            continue

        _debug_ads_raw(current_payload, report_date)
        rows_before = len(current_rows)
        for campaign in current_payload:
            loaded_campaign_ids.add(str(_campaign_id(campaign) or campaign_id))
            _append_campaign_rows(current_rows, campaign)

        campaign_rows_count = len(current_rows) - rows_before
        if previous_payload is None and (
            _ADS_RATE_LIMIT_STATS.get("stopped_by_time_limit") or previous_status == 429
        ):
            _ADS_RATE_LIMIT_STATS["partial"] = True
            partial_campaign_ids.add(str(campaign_id))
            result_status = "partial"
        else:
            result_status = "success"
            success_campaign_ids.add(str(campaign_id))

        if previous_payload:
            for campaign in previous_payload:
                _append_campaign_rows(previous_rows, campaign)

        if time.monotonic() >= campaign_deadline:
            result_status = "timeout"
            _ADS_RATE_LIMIT_STATS["partial"] = True
            partial_campaign_ids.add(str(campaign_id))
            success_campaign_ids.discard(str(campaign_id))
            _log_ads_campaign_time_limit(
                campaign_id, time.monotonic() - campaign_started_at
            )

        _log_ads_campaign_result(campaign_id, result_status, campaign_rows_count)
        _update_campaign_health(
            seller_id,
            campaign_id,
            result_status,
            rows=campaign_rows_count,
            error_code=(
                None
                if result_status == "success"
                else str(previous_status or current_status or result_status)
            ),
        )

    _ADS_RATE_LIMIT_STATS["campaigns_requested"] = len(campaign_ids or [])
    _ADS_RATE_LIMIT_STATS["campaigns_loaded"] = len(
        {cid for cid in loaded_campaign_ids if cid}
    )
    _ADS_RATE_LIMIT_STATS["campaigns_attempted"] = len(attempted_campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_success"] = len(success_campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_partial"] = len(partial_campaign_ids)
    _ADS_RATE_LIMIT_STATS["campaigns_failed"] = len(failed_campaign_ids)
    _ADS_RATE_LIMIT_STATS["partial"] = bool(_ADS_RATE_LIMIT_STATS.get("partial"))
    _ADS_RATE_LIMIT_STATS["partial_rows_saved"] = len(current_rows)

    for row in current_rows:
        row["date"] = current_date
        row["selectedPeriod"] = current_date
        row["pastPeriod"] = previous_date

    return _merge_previous_period(current_rows, previous_rows)


def _storage():
    try:
        from app.storage import supabase_storage as storage
    except Exception as error:
        print(f"WARNING: Ads Supabase storage unavailable: {error}")
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
    print("ADS CAMPAIGN CACHE:")
    print(f"source: {source}")
    print(f"campaigns: {len(campaigns or [])}")
    print(f"cache age: {age_text}")
    print(f"force refresh: {str(bool(force_refresh)).lower()}")


def _load_campaigns(token, seller_id):
    storage = _storage()
    force_refresh = _env_bool("WB_ADS_FORCE_REFRESH_CAMPAIGNS", False)
    cached_campaigns = []
    if storage and hasattr(storage, "get_ads_campaigns_cache"):
        cached_campaigns = storage.get_ads_campaigns_cache(seller_id)
    if not force_refresh and _campaign_cache_is_fresh(cached_campaigns):
        _campaign_cache_log("cache", cached_campaigns, force_refresh)
        return cached_campaigns, 200

    api_campaigns, campaign_status = _request_ads_campaign_ids(token)
    if api_campaigns is None:
        _campaign_cache_log("api", cached_campaigns, force_refresh)
        return None, campaign_status
    if storage and hasattr(storage, "save_ads_campaigns_cache"):
        storage.save_ads_campaigns_cache(seller_id, api_campaigns)
        cached_campaigns = storage.get_ads_campaigns_cache(seller_id)
    campaigns = cached_campaigns or api_campaigns
    _campaign_cache_log("api", campaigns, force_refresh)
    return campaigns, campaign_status


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
    max_per_run = max(1, _env_int("WB_ADS_MAX_CAMPAIGNS_PER_RUN", 5))
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
            print("ADS CAMPAIGN SKIPPED:")
            print(f"campaign_id: {campaign.get('campaign_id')}")
            print("reason: repeated errors cooldown")
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
    print("ADS PRIORITY QUEUE:")
    print(f"campaigns total: {len(campaigns or [])}")
    print(f"campaigns selected: {len(selected)}")
    print(f"campaigns skipped: {skipped}")
    print(
        "selection reason: TOP drops, OOS forecast, no stats, oldest stats update, then active status"
    )
    if cooldown_skipped:
        print(f"campaigns skipped by repeated errors cooldown: {cooldown_skipped}")
    selected_ids = {str(row.get("campaign_id")) for row in selected}
    for campaign in eligible_campaigns:
        campaign_id = str(campaign.get("campaign_id"))
        if campaign_id and campaign_id not in selected_ids:
            print("ADS CAMPAIGN NEXT RUN:")
            print(f"campaign_id: {campaign_id}")
            print("reason: campaign remains queued for a future run")
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
    report_date=None, seller_id=None, top_drop_nm_ids=None, oos_nm_ids=None
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

    seller_id = (
        seller_id or os.getenv("SELLER_ID") or os.getenv("WB_SELLER_ID") or "default"
    )
    campaigns, campaign_status = _load_campaigns(token, seller_id)

    if campaigns is None:
        if _is_stub_status(campaign_status):
            print("Ads collector работает в stub mode")
        else:
            print("Ads collector fallback to stub mode")
        print("Ads rows: 0")
        return []

    print(f"ADS CAMPAIGNS FOUND: {len(campaigns)}")

    if not campaigns:
        print("Ads collector работает в stub mode")
        print("Ads rows: 0")
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
    print("ADS COLLECTION SUMMARY:")
    print(
        f"campaigns attempted: {_ADS_RATE_LIMIT_STATS.get('campaigns_attempted', len(campaign_ids))}"
    )
    print(f"campaigns success: {_ADS_RATE_LIMIT_STATS.get('campaigns_success', 0)}")
    print(f"campaigns partial: {_ADS_RATE_LIMIT_STATS.get('campaigns_partial', 0)}")
    print(f"campaigns failed: {_ADS_RATE_LIMIT_STATS.get('campaigns_failed', 0)}")
    print(f"ads rows total: {len(ads_rows)}")
    elapsed = round(time.monotonic() - collect_started_at, 2)
    _ADS_RATE_LIMIT_STATS["elapsed_seconds"] = elapsed
    print("ADS COLLECTOR TIME LIMIT:")
    print(f"max seconds: {max_collect_seconds}")
    print(f"elapsed: {elapsed}")
    print(
        "stopped by limit: "
        f"{str(bool(_ADS_RATE_LIMIT_STATS.get('stopped_by_time_limit'))).lower()}"
    )
    print(
        f"partial rows saved: {_ADS_RATE_LIMIT_STATS.get('partial_rows_saved', len(ads_rows))}"
    )
    print("ADS COVERAGE:")
    print(f"campaigns: {len(campaign_ids) or len(row_campaign_ids)}")
    print(f"ads rows: {len(ads_rows)}")
    print(f"unique nmIds: {len(unique_nmids)}")
    total_campaigns = len(campaigns) or len(row_campaign_ids)
    processed_campaigns = _ADS_RATE_LIMIT_STATS.get("campaigns_loaded") or len(
        row_campaign_ids
    )
    confidence = _coverage_confidence(processed_campaigns, total_campaigns)
    _ADS_RATE_LIMIT_STATS["adsCoverageConfidence"] = confidence
    print(f"coverage confidence: {confidence}")
    print(f"Ads rows: {len(ads_rows)}")
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
