import os
import re
import time
from datetime import datetime, timedelta

import requests

import app.config as wb_config
from app.collectors.ads import _ads_error_summary, _load_campaigns
from app.storage.supabase_storage import _get_client

ADS_NORMQUERY_STATS_URL = "https://advert-api.wildberries.ru/adv/v0/normquery/stats"
ADS_DAILY_NORMQUERY_STATS_URL = "https://advert-api.wildberries.ru/adv/v1/normquery/stats"
ADS_CLUSTERS_TIMEOUT_SECONDS = 60
def _env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


ADS_CLUSTER_MAX_CAMPAIGNS_PER_SELLER = _env_int(
    "ADS_CLUSTER_MAX_CAMPAIGNS_PER_SELLER", 20
)
ADS_CLUSTERS_REQUEST_PAUSE_SECONDS = float(os.getenv("ADS_CLUSTERS_REQUEST_PAUSE_SECONDS", "6.5"))
ADS_CLUSTER_FORCE_CAMPAIGN_IDS_ENV = "ADS_CLUSTER_FORCE_CAMPAIGN_IDS"

ADS_CLUSTERS_AUDIT_CAMPAIGN_ID = 31971499
ADS_CLUSTERS_AUDIT_REPORT_DATE = "2026-06-20"

LOG_LEVEL = os.getenv("LOG_LEVEL", "summary").strip().lower()


def _debug_log(*args):
    if LOG_LEVEL == "debug":
        print(*args)


def _summary_log(*args):
    print(*args)


def _to_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _to_number(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("%", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def _first_present(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _first_present_with_key(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return key, value
    return None, default


def _is_audit_campaign(campaign, report_date):
    return (
        _to_int(campaign.get("campaign_id")) == ADS_CLUSTERS_AUDIT_CAMPAIGN_ID
        and str(report_date) == ADS_CLUSTERS_AUDIT_REPORT_DATE
    )


def _metric_values(row):
    spend = _first_present(row, ["sum", "spend", "expense", "expenses", "cost"])
    return {
        "views": _to_int(_first_present(row, ["views", "impressions", "shows"])) or 0,
        "clicks": _to_int(row.get("clicks")) or 0,
        "atbs": _to_int(
            _first_present(row, ["atbs", "cart_count", "cartCount", "carts"])
        )
        or 0,
        "orders": _to_int(row.get("orders")) or 0,
        "shks": _to_int(row.get("shks")) or 0,
        "spend": _to_number(spend) or 0,
    }


def _empty_totals():
    return {
        "views": 0,
        "clicks": 0,
        "atbs": 0,
        "orders": 0,
        "shks": 0,
        "spend": 0,
    }


def _add_totals(totals, values):
    for key in totals:
        totals[key] += values.get(key) or 0


def _totals_from_rows(rows):
    totals = _empty_totals()
    for row in rows or []:
        values = {
            "views": row.get("impressions") or 0,
            "clicks": row.get("clicks") or 0,
            "atbs": row.get("cart_count") or 0,
            "orders": (row.get("raw_json") or {}).get("orders") or 0,
            "shks": row.get("orders_count") or 0,
            "spend": row.get("spend") or 0,
        }
        _add_totals(totals, values)
    return totals


def _audit_response_shape(payload):
    if not isinstance(payload, dict):
        return 0, 0
    total_items = 0
    total_daily_stats = 0
    items = payload.get("items")
    if isinstance(items, list):
        total_items += len(items)
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("dailyStats"), list):
                total_daily_stats += len(item.get("dailyStats") or [])
    stats = payload.get("stats")
    if isinstance(stats, list):
        total_items += len(stats)
        for item in stats:
            if isinstance(item, dict) and isinstance(item.get("stats"), list):
                total_daily_stats += len(item.get("stats") or [])
    return total_items, total_daily_stats


def _log_skipped_audit_row(reason, item, campaign_id):
    values = _metric_values(item)
    _summary_log(
        "ADS CLUSTERS AUDIT SKIPPED ROW: "
        f"reason={reason} "
        f"advertId={_first_present(item, ['advertId', 'advert_id', 'campaignId', 'campaign_id'], campaign_id)} "
        f"nmId={_first_present(item, ['nmId', 'nm_id', 'nm'])} "
        f"normQuery={_cluster_value(item)} "
        f"views={values['views']} clicks={values['clicks']} atbs={values['atbs']} "
        f"orders={values['orders']} shks={values['shks']} spend={values['spend']}"
    )


def _log_audit_summary(payload, campaign, report_date, response_rows, saved_rows=None):
    if not _is_audit_campaign(campaign, report_date):
        return
    total_items, total_daily_stats = _audit_response_shape(payload)
    totals = _empty_totals()
    extracted_rows = _extract_rows_from_v0(payload, campaign) + _extract_rows_from_v1(
        payload, campaign
    )
    if not extracted_rows:
        extracted_rows = list(_iter_dicts(payload))
    for item in extracted_rows:
        _add_totals(totals, _metric_values(item))
    api_zero_click_paid_views = 0
    for item in extracted_rows:
        values = _metric_values(item)
        if (
            values["views"] > 0
            and values["spend"] > 0
            and values["clicks"] == 0
            and values["orders"] == 0
            and values["shks"] == 0
        ):
            api_zero_click_paid_views += 1
    skipped_rows = max(len(extracted_rows) - len(response_rows or []), 0)
    _summary_log(
        "ADS CLUSTERS AUDIT SUMMARY: "
        f"campaign_id={campaign.get('campaign_id')} report_date={report_date} "
        f"total_items={total_items} total_dailyStats={total_daily_stats} "
        f"extracted_rows={len(response_rows or [])} skipped_rows={skipped_rows} "
        f"sum_views={totals['views']} sum_clicks={totals['clicks']} "
        f"sum_atbs={totals['atbs']} sum_orders={totals['orders']} "
        f"sum_shks={totals['shks']} sum_spend={round(totals['spend'], 2)} "
        f"api_rows_views_gt_0_spend_gt_0_clicks_0_orders_0_shks_0={api_zero_click_paid_views}"
    )
    if saved_rows is None:
        return
    saved_totals = _totals_from_rows(saved_rows)
    saved_zero_click_paid_views = 0
    for row in saved_rows or []:
        raw_json = row.get("raw_json") or {}
        if (
            (row.get("impressions") or 0) > 0
            and (row.get("spend") or 0) > 0
            and (row.get("clicks") or 0) == 0
            and (_to_int(raw_json.get("orders")) or 0) == 0
            and (row.get("orders_count") or 0) == 0
        ):
            saved_zero_click_paid_views += 1
    diff_totals = {
        key: round(totals[key] - saved_totals[key], 2)
        for key in totals
    }
    _summary_log(f"WB_API_TOTALS campaign_id={campaign.get('campaign_id')} {totals}")
    _summary_log(
        f"SAVED_TOTALS campaign_id={campaign.get('campaign_id')} {saved_totals}"
    )
    _summary_log(
        f"DIFF_TOTALS campaign_id={campaign.get('campaign_id')} {diff_totals}"
    )
    _summary_log(
        "ADS CLUSTERS AUDIT ZERO_CLICK_PAID_VIEWS: "
        f"campaign_id={campaign.get('campaign_id')} "
        f"api_rows={api_zero_click_paid_views} saved_rows={saved_zero_click_paid_views}"
    )


def _safe_cpo(spend, count):
    spend = _to_number(spend) or 0
    count = _to_number(count) or 0
    if not count:
        return None
    return round(spend / count, 2)


def _iter_dicts(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _iter_dicts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_dicts(item)


def _response_reason(response):
    if response.status_code == 400:
        return "invalid payload"
    if response.status_code in (403, 404):
        return "unsupported endpoint"
    if response.status_code == 429:
        return "rate limited"
    return "request failed"


def _log_ads_clusters_request(endpoint, method, campaign_id, status_code, body, payload):
    _summary_log(
        "ADS CLUSTERS REQUEST: "
        f"endpoint={endpoint} method={method} campaign_id={campaign_id} "
        f"status_code={status_code} payload={payload} response_body={body}"
    )


def _remember_ads_clusters_request(campaign, endpoint, payload, response):
    campaign["ads_clusters_last_request"] = {
        "request_endpoint": endpoint,
        "request_payload": payload,
        "response_status": getattr(response, "status_code", None),
        "response_body": getattr(response, "text", None),
    }


def _cluster_value(row):
    return _first_present(
        row,
        [
            "norm_query",
            "normQuery",
            "cluster",
            "query",
            "keyword",
            "phrase",
            "word",
            "name",
        ],
    )


def _extract_rows_from_v0(payload, campaign):
    rows = []
    for item in payload.get("stats") or [] if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        base = {
            "advertId": item.get("advert_id"),
            "nmId": item.get("nm_id"),
        }
        for stat in item.get("stats") or []:
            if isinstance(stat, dict):
                rows.append({**base, **stat})
    return rows


def _extract_rows_from_v1(payload, campaign):
    rows = []
    for item in payload.get("items") or [] if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        base = {
            "advertId": item.get("advertId"),
            "nmId": item.get("nmId"),
        }
        for daily_stat in item.get("dailyStats") or []:
            if not isinstance(daily_stat, dict):
                continue
            stat = daily_stat.get("stat")
            if isinstance(stat, dict):
                rows.append({**base, **stat, "date": daily_stat.get("date")})
    return rows


def _extract_cluster_rows(payload, campaign, report_date=None):
    rows = []
    campaign_id = campaign.get("campaign_id")
    audit_enabled = _is_audit_campaign(campaign, report_date)
    extracted_rows = _extract_rows_from_v0(payload, campaign) + _extract_rows_from_v1(
        payload, campaign
    )
    if not extracted_rows:
        extracted_rows = list(_iter_dicts(payload))

    for item in extracted_rows:
        cluster = _cluster_value(item)
        if cluster in (None, ""):
            if audit_enabled:
                _log_skipped_audit_row("missing normQuery", item, campaign_id)
            continue

        spend = _first_present(item, ["sum", "spend", "expense", "expenses", "cost"])
        cart_count = _first_present(item, ["atbs", "cart_count", "cartCount", "carts"])
        # orders_count для отчёта по кластерам соответствует колонке WB
        # "Заказанные товары, шт", поэтому используется shks, а не orders.
        orders_count_source, orders_count = _first_present_with_key(
            item,
            [
                "shks",
                "ordered_products",
                "orderedProducts",
                "orders_count",
                "ordersCount",
                "orders",
            ],
        )
        normalized_orders_count = _to_int(orders_count)

        _summary_log(
            "ADS CLUSTERS ROW AUDIT: "
            f"campaign_id={_to_int(_first_present(item, ['advertId', 'campaignId', 'campaign_id'], campaign_id))} "
            f"cluster={cluster} "
            f"clicks={_to_int(item.get('clicks'))} "
            f"cart_count={_to_int(cart_count)} "
            f"orders_count={normalized_orders_count} "
            f"orders_count_source={orders_count_source}"
        )

        rows.append(
            {
                "campaign_id": _to_int(
                    _first_present(
                        item, ["advertId", "campaignId", "campaign_id"], campaign_id
                    )
                ),
                "campaign_name": _first_present(
                    item, ["campaignName", "campaign_name"], campaign.get("campaign_name")
                ),
                "campaign_type": _first_present(
                    item,
                    ["campaignType", "campaign_type", "type"],
                    campaign.get("campaign_type"),
                ),
                "nm_id": _to_int(_first_present(item, ["nmId", "nm_id", "nm"])),
                "vendor_code": _first_present(item, ["vendorCode", "vendor_code"]),
                "title": _first_present(item, ["title", "name", "subjectName"]),
                "cluster": str(cluster),
                "impressions": _to_int(
                    _first_present(item, ["views", "impressions", "shows"])
                ),
                "clicks": _to_int(item.get("clicks")),
                "ctr": _to_number(item.get("ctr")),
                "cpc": _to_number(item.get("cpc")),
                "spend": _to_number(spend),
                "cart_count": _to_int(cart_count),
                "orders_count": normalized_orders_count,
                "cpo_cart": _safe_cpo(spend, cart_count),
                "cpo_order": _safe_cpo(spend, normalized_orders_count),
                "raw_json": item,
            }
        )

    return rows


def _campaign_nm_ids(campaign):
    nm_ids = campaign.get("nm_ids") or campaign.get("nmIds") or []
    if campaign.get("nm_id") not in (None, ""):
        nm_ids = [campaign.get("nm_id"), *nm_ids]
    normalized = []
    seen = set()
    for nm_id in nm_ids:
        value = _to_int(nm_id)
        if value is None or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _request_normquery_stats(token, campaign, report_date):
    campaign_id = campaign.get("campaign_id")
    nm_ids = _campaign_nm_ids(campaign)
    payload = {
        "from": str(report_date),
        "to": str(report_date),
        "items": [
            {"advert_id": _to_int(campaign_id), "nm_id": nm_id} for nm_id in nm_ids
        ],
    }
    response = requests.post(
        ADS_NORMQUERY_STATS_URL,
        headers={"Authorization": token},
        json=payload,
        timeout=ADS_CLUSTERS_TIMEOUT_SECONDS,
    )
    _remember_ads_clusters_request(campaign, ADS_NORMQUERY_STATS_URL, payload, response)
    _log_ads_clusters_request(
        ADS_NORMQUERY_STATS_URL,
        "POST",
        campaign_id,
        response.status_code,
        response.text,
        payload,
    )
    if response.status_code != 200:
        _summary_log(
            f"ADS CLUSTERS: {_response_reason(response)} "
            f"{_ads_error_summary(response)} campaign_id={campaign_id}"
        )
        return None
    try:
        return response.json()
    except ValueError:
        _summary_log(f"ADS CLUSTERS: invalid JSON campaign_id={campaign_id}")
        return None


def _request_daily_normquery_stats(token, campaign, report_date):
    campaign_id = campaign.get("campaign_id")
    nm_ids = _campaign_nm_ids(campaign)
    payload = {
        "from": str(report_date),
        "to": str(report_date),
        "items": [
            {"advertId": _to_int(campaign_id), "nmId": nm_id} for nm_id in nm_ids
        ],
    }
    response = requests.post(
        ADS_DAILY_NORMQUERY_STATS_URL,
        headers={"Authorization": token},
        json=payload,
        timeout=ADS_CLUSTERS_TIMEOUT_SECONDS,
    )
    _remember_ads_clusters_request(campaign, ADS_DAILY_NORMQUERY_STATS_URL, payload, response)
    _log_ads_clusters_request(
        ADS_DAILY_NORMQUERY_STATS_URL,
        "POST",
        campaign_id,
        response.status_code,
        response.text,
        payload,
    )
    if response.status_code != 200:
        _summary_log(
            f"ADS CLUSTERS: {_response_reason(response)} "
            f"{_ads_error_summary(response)} campaign_id={campaign_id}"
        )
        return None
    try:
        return response.json()
    except ValueError:
        _summary_log(f"ADS CLUSTERS: invalid JSON campaign_id={campaign_id}")
        return None


def _request_campaign_clusters(token, campaign, report_date):
    payload = _request_daily_normquery_stats(token, campaign, report_date)
    rows = (
        _extract_cluster_rows(payload, campaign, report_date)
        if payload is not None
        else []
    )
    if rows:
        return rows, payload

    time.sleep(ADS_CLUSTERS_REQUEST_PAUSE_SECONDS)
    payload = _request_normquery_stats(token, campaign, report_date)
    rows = (
        _extract_cluster_rows(payload, campaign, report_date)
        if payload is not None
        else []
    )
    return rows, payload


def _normalize_save_row(row, report_date, seller_id, seller_name):
    return {
        "report_date": str(report_date),
        "seller_id": _to_int(seller_id),
        "seller_name": seller_name,
        "campaign_id": row.get("campaign_id"),
        "campaign_name": row.get("campaign_name"),
        "campaign_type": row.get("campaign_type"),
        "nm_id": row.get("nm_id"),
        "vendor_code": row.get("vendor_code"),
        "title": row.get("title"),
        "cluster": row.get("cluster"),
        "impressions": row.get("impressions"),
        "clicks": row.get("clicks"),
        "ctr": row.get("ctr"),
        "cpc": row.get("cpc"),
        "spend": row.get("spend"),
        "cart_count": row.get("cart_count"),
        "orders_count": row.get("orders_count"),
        "cpo_cart": row.get("cpo_cart"),
        "cpo_order": row.get("cpo_order"),
        "raw_json": row.get("raw_json"),
    }


def _save_ads_clusters(rows, report_date, seller_id, seller_name):
    normalized_rows = [
        _normalize_save_row(row, report_date, seller_id, seller_name)
        for row in rows or []
        if row.get("campaign_id") not in (None, "")
        and row.get("cluster") not in (None, "")
    ]
    if not normalized_rows:
        return 0

    _get_client().table("ads_clusters_daily").upsert(
        normalized_rows,
        on_conflict="report_date,seller_id,campaign_id,nm_id,cluster",
    ).execute()
    return len(normalized_rows)


def _load_active_ads_metric_campaigns(report_date, seller_id, limit):
    try:
        rows = (
            _get_client()
            .table("daily_ads_metrics")
            .select(
                "campaign_id,campaign_name,campaign_type,nm_id,vendor_code,title,"
                "impressions,clicks,spend,orders_count"
            )
            .eq("report_date", str(report_date))
            .eq("seller_id", _to_int(seller_id))
            .execute()
            .data
        )
    except Exception as error:
        _summary_log(f"ADS CLUSTERS: daily_ads_metrics read error error={error}")
        return [], []

    campaigns_by_id = {}
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        campaign_id = row.get("campaign_id")
        if campaign_id in (None, ""):
            continue
        campaign = campaigns_by_id.setdefault(
            str(campaign_id),
            {
                "campaign_id": campaign_id,
                "campaign_name": row.get("campaign_name"),
                "campaign_type": row.get("campaign_type"),
                "nm_ids": [],
                "vendor_code": row.get("vendor_code"),
                "title": row.get("title"),
                "spend": 0,
                "orders_count": 0,
                "impressions": 0,
            },
        )
        campaign["campaign_name"] = campaign["campaign_name"] or row.get("campaign_name")
        campaign["campaign_type"] = campaign["campaign_type"] or row.get("campaign_type")
        campaign["vendor_code"] = campaign["vendor_code"] or row.get("vendor_code")
        campaign["title"] = campaign["title"] or row.get("title")
        campaign["spend"] += _to_number(row.get("spend")) or 0
        campaign["orders_count"] += _to_number(row.get("orders_count")) or 0
        campaign["impressions"] += _to_number(row.get("impressions")) or 0
        nm_id = _to_int(row.get("nm_id"))
        if nm_id is not None and nm_id not in campaign["nm_ids"]:
            campaign["nm_ids"].append(nm_id)

    campaigns = sorted(
        campaigns_by_id.values(),
        key=lambda campaign: (
            campaign.get("spend") or 0,
            campaign.get("orders_count") or 0,
            campaign.get("impressions") or 0,
        ),
        reverse=True,
    )
    return campaigns[:limit], campaigns[limit:]


def _parse_force_campaign_ids():
    raw_value = os.getenv(ADS_CLUSTER_FORCE_CAMPAIGN_IDS_ENV, "")
    campaign_ids = []
    seen = set()
    for part in re.split(r"[\s,]+", raw_value):
        campaign_id = _to_int(part.strip())
        if campaign_id is None or campaign_id in seen:
            continue
        seen.add(campaign_id)
        campaign_ids.append(campaign_id)
    return campaign_ids


def _load_force_ads_metric_campaigns(report_date, seller_id, force_campaign_ids):
    if not force_campaign_ids:
        return []

    _summary_log("ADS CLUSTERS FORCE IDS:")
    for campaign_id in force_campaign_ids:
        _summary_log(campaign_id)

    try:
        rows = (
            _get_client()
            .table("daily_ads_metrics")
            .select(
                "campaign_id,campaign_name,campaign_type,nm_id,vendor_code,title"
            )
            .eq("report_date", str(report_date))
            .eq("seller_id", _to_int(seller_id))
            .in_("campaign_id", force_campaign_ids)
            .execute()
            .data
        )
    except Exception as error:
        _summary_log(f"ADS CLUSTERS FORCE: daily_ads_metrics read error error={error}")
        rows = []

    campaigns_by_id = {
        str(campaign_id): {
            "campaign_id": campaign_id,
            "campaign_name": None,
            "campaign_type": None,
            "nm_ids": [],
            "vendor_code": None,
            "title": None,
            "force_ads_clusters": True,
        }
        for campaign_id in force_campaign_ids
    }

    for row in rows or []:
        if not isinstance(row, dict):
            continue
        campaign_id = _to_int(row.get("campaign_id"))
        if campaign_id is None or str(campaign_id) not in campaigns_by_id:
            continue

        campaign = campaigns_by_id[str(campaign_id)]
        campaign["campaign_name"] = campaign["campaign_name"] or row.get("campaign_name")
        campaign["campaign_type"] = campaign["campaign_type"] or row.get("campaign_type")
        campaign["vendor_code"] = campaign["vendor_code"] or row.get("vendor_code")
        campaign["title"] = campaign["title"] or row.get("title")
        nm_id = _to_int(row.get("nm_id"))
        if nm_id is not None and nm_id not in campaign["nm_ids"]:
            campaign["nm_ids"].append(nm_id)

    return list(campaigns_by_id.values())


def _merge_force_campaigns(campaigns, force_campaigns):
    merged_by_id = {
        str(campaign.get("campaign_id")): campaign
        for campaign in campaigns or []
        if campaign.get("campaign_id") not in (None, "")
    }

    for force_campaign in force_campaigns or []:
        campaign_id = force_campaign.get("campaign_id")
        if campaign_id in (None, ""):
            continue

        existing = merged_by_id.get(str(campaign_id), {})
        nm_ids = _campaign_nm_ids(existing) + _campaign_nm_ids(force_campaign)
        force_campaign = {
            **existing,
            **force_campaign,
            "nm_ids": [],
            "force_ads_clusters": True,
        }
        for nm_id in nm_ids:
            if nm_id not in force_campaign["nm_ids"]:
                force_campaign["nm_ids"].append(nm_id)
        merged_by_id[str(campaign_id)] = force_campaign

    return list(merged_by_id.values())


def _prepared_ads_cluster_rows_count(rows):
    return len(
        [
            row
            for row in rows or []
            if row.get("campaign_id") not in (None, "")
            and row.get("cluster") not in (None, "")
        ]
    )


def _log_force_processing(
    seller_id, campaign, report_date, nm_ids, clusters_received, rows_prepared, rows_saved
):
    request_info = campaign.get("ads_clusters_last_request") or {}
    _summary_log("ADS CLUSTERS FORCE PROCESSING:")
    _summary_log(f"seller_id={seller_id}")
    _summary_log(f"campaign_id={campaign.get('campaign_id')}")
    _summary_log(f"report_date={report_date}")
    _summary_log(f"nm_ids_found={len(nm_ids)}")
    _summary_log(f"nm_ids_list={nm_ids}")
    _summary_log(f"request_endpoint={request_info.get('request_endpoint')}")
    _summary_log(f"request_payload={request_info.get('request_payload')}")
    _summary_log(f"response_status={request_info.get('response_status')}")
    _summary_log(f"response_body={request_info.get('response_body')}")
    _summary_log(f"clusters_received={clusters_received}")
    _summary_log(f"rows_prepared={rows_prepared}")
    _summary_log(f"rows_saved={rows_saved}")


def _merge_campaign_metadata(active_campaigns, campaigns):
    by_id = {
        str(campaign.get("campaign_id")): campaign
        for campaign in campaigns or []
        if campaign.get("campaign_id") not in (None, "")
    }
    merged = []
    for active_campaign in active_campaigns or []:
        campaign = {
            **by_id.get(str(active_campaign.get("campaign_id")), {}),
            **active_campaign,
        }
        merged.append(campaign)
    return merged


def collect_ads_clusters(
    report_date=None, seller_id=None, seller_name=None, campaigns=None
):
    report_date = report_date or (datetime.now().date() - timedelta(days=1))
    token_source = wb_config.CURRENT_WB_TOKEN_SECRET_NAME
    token = wb_config.WB_API_TOKEN or os.getenv(token_source)

    if not token:
        _summary_log("ADS CLUSTERS: token not configured")
        return []

    seller_id = seller_id or os.getenv("SELLER_ID") or os.getenv("WB_SELLER_ID")
    wb_campaigns = []
    if campaigns is not None:
        wb_campaigns = campaigns or []
    else:
        try:
            wb_campaigns, _ = _load_campaigns(token, seller_id)
        except Exception as error:
            _summary_log(f"ADS CLUSTERS: WB campaigns metadata read error error={error}")
            wb_campaigns = []

    active_campaigns, skipped_campaigns = _load_active_ads_metric_campaigns(
        report_date, seller_id, ADS_CLUSTER_MAX_CAMPAIGNS_PER_SELLER
    )
    force_campaign_ids = _parse_force_campaign_ids()
    campaigns = _merge_campaign_metadata(active_campaigns, wb_campaigns)
    skipped_campaign_ids = [
        campaign.get("campaign_id")
        for campaign in skipped_campaigns
        if _to_int(campaign.get("campaign_id")) not in force_campaign_ids
    ]

    _summary_log(
        "ADS CLUSTERS SELECTION: "
        f"seller_id={seller_id} campaigns_available={len(active_campaigns) + len(skipped_campaigns)} "
        f"campaigns_selected={len(campaigns)} "
        f"selected_campaign_ids={[campaign.get('campaign_id') for campaign in campaigns]} "
        f"skipped_by_limit={len(skipped_campaign_ids)} "
        f"skipped_campaign_ids={skipped_campaign_ids}"
    )
    for campaign_id in skipped_campaign_ids:
        _summary_log(f"ADS CLUSTERS SKIPPED BY LIMIT campaign_id={campaign_id}")
    if not active_campaigns:
        _summary_log("ADS CLUSTERS: no active daily_ads_metrics campaigns found")

    force_campaigns = _load_force_ads_metric_campaigns(
        report_date, seller_id, force_campaign_ids
    )
    campaigns = _merge_force_campaigns(campaigns, force_campaigns)
    selected_campaign_ids = [campaign.get("campaign_id") for campaign in campaigns]
    if force_campaigns:
        _summary_log(
            "ADS CLUSTERS SELECTION AFTER FORCE: "
            f"seller_id={seller_id} campaigns_selected={len(campaigns)} "
            f"selected_campaign_ids={selected_campaign_ids}"
        )

    processed = 0
    total_clusters = 0
    saved_rows = 0
    campaigns_saved = 0
    no_data_campaigns = []
    force_campaigns_processed = 0
    force_campaigns_saved = 0
    force_campaigns_no_data = 0
    all_rows = []

    for campaign in campaigns:
        campaign_id = campaign.get("campaign_id")
        is_force_campaign = bool(campaign.get("force_ads_clusters"))
        nm_ids = _campaign_nm_ids(campaign)
        nm_ids_found = len(nm_ids)
        if campaign_id in (None, ""):
            continue
        if is_force_campaign:
            force_campaigns_processed += 1
        if not nm_ids_found:
            _summary_log(
                f"ADS CLUSTERS: invalid payload campaign_id={campaign_id} "
                "reason=no nm_id from daily_ads_metrics"
            )
            no_data_campaigns.append(campaign_id)
            _summary_log(
                "ADS CLUSTERS CAMPAIGN: "
                f"campaign_id={campaign_id} nm_ids_found=0 "
                "clusters_received=0 rows_saved=0"
            )
            if is_force_campaign:
                force_campaigns_no_data += 1
                _summary_log(f"ADS CLUSTERS FORCE NO NM_IDS campaign_id={campaign_id}")
                _log_force_processing(
                    seller_id, campaign, report_date, nm_ids, 0, 0, 0
                )
            continue
        processed += 1
        audit_payload = None
        try:
            rows, audit_payload = _request_campaign_clusters(
                token, campaign, report_date
            )
        except requests.RequestException as error:
            _summary_log(
                f"ADS CLUSTERS: WB API error campaign_id={campaign_id} error={error}"
            )
            rows = []
        except Exception as error:
            _summary_log(
                f"ADS CLUSTERS: error campaign_id={campaign_id} error={error}"
            )
            rows = []

        campaign_saved_rows = 0
        rows_prepared = _prepared_ads_cluster_rows_count(rows)
        if not rows:
            no_data_campaigns.append(campaign_id)
            if is_force_campaign:
                force_campaigns_no_data += 1
                _summary_log(f"ADS CLUSTERS FORCE NO DATA campaign_id={campaign_id}")
            _summary_log(f"ADS CLUSTERS NO DATA campaign_id={campaign_id}")
            _summary_log(
                f"ADS CLUSTERS: no cluster data for campaign_id={campaign_id}"
            )
            _summary_log(
                "ADS CLUSTERS CAMPAIGN: "
                f"campaign_id={campaign_id} nm_ids_found={nm_ids_found} "
                "clusters_received=0 rows_saved=0"
            )
            if is_force_campaign:
                _log_force_processing(
                    seller_id, campaign, report_date, nm_ids, 0, rows_prepared, 0
                )
            _log_audit_summary(audit_payload, campaign, report_date, rows, [])
            time.sleep(ADS_CLUSTERS_REQUEST_PAUSE_SECONDS)
            continue

        total_clusters += len(rows)
        saved_audit_rows = [
            row
            for row in rows
            if row.get("campaign_id") not in (None, "")
            and row.get("cluster") not in (None, "")
        ]
        try:
            campaign_saved_rows = _save_ads_clusters(
                rows, report_date, seller_id, seller_name
            )
            saved_rows += campaign_saved_rows
            if campaign_saved_rows:
                campaigns_saved += 1
                if is_force_campaign:
                    force_campaigns_saved += 1
        except Exception as error:
            _summary_log(
                f"ADS CLUSTERS: Supabase save error campaign_id={campaign_id} "
                f"error={error}"
            )
            if is_force_campaign:
                _summary_log(
                    f"ADS CLUSTERS FORCE SAVE FAILED campaign_id={campaign_id} "
                    f"error={repr(error)}"
                )
        _summary_log(
            "ADS CLUSTERS CAMPAIGN: "
            f"campaign_id={campaign_id} nm_ids_found={nm_ids_found} "
            f"clusters_received={len(rows)} rows_saved={campaign_saved_rows}"
        )
        _log_audit_summary(
            audit_payload,
            campaign,
            report_date,
            rows,
            saved_audit_rows if campaign_saved_rows else [],
        )
        if is_force_campaign:
            if rows and campaign_saved_rows <= 0:
                _summary_log(
                    f"ADS CLUSTERS FORCE SAVE FAILED campaign_id={campaign_id} "
                    "error=upsert returned zero saved rows"
                )
            _log_force_processing(
                seller_id,
                campaign,
                report_date,
                nm_ids,
                len(rows),
                rows_prepared,
                campaign_saved_rows,
            )
        all_rows.extend(rows)
        time.sleep(ADS_CLUSTERS_REQUEST_PAUSE_SECONDS)

    campaigns_available = len(active_campaigns) + len(skipped_campaigns)
    campaigns_without_clusters = len(no_data_campaigns)
    skipped_by_limit = len(skipped_campaign_ids)
    _summary_log(
        "ADS CLUSTERS RESULT: "
        f"seller_id={seller_id} campaigns_available={campaigns_available} "
        f"campaigns_selected={len(selected_campaign_ids)} "
        f"campaigns_saved={campaigns_saved} "
        f"campaigns_without_clusters={campaigns_without_clusters} "
        f"force_campaign_ids={force_campaign_ids} "
        f"force_campaigns_processed={force_campaigns_processed} "
        f"force_campaigns_saved={force_campaigns_saved} "
        f"force_campaigns_no_data={force_campaigns_no_data} "
        f"skipped_by_limit={skipped_by_limit} "
        f"selected_campaign_ids={selected_campaign_ids} "
        f"skipped_campaign_ids={skipped_campaign_ids} rows_saved={saved_rows}"
    )
    _summary_log(
        "ADS CLUSTERS: "
        f"campaigns_found={len(campaigns)} campaigns_processed={processed} "
        f"clusters_received={total_clusters} rows_saved={saved_rows} "
        f"no_data_campaigns={len(no_data_campaigns)}"
    )
    if no_data_campaigns:
        _summary_log(
            "ADS CLUSTERS: no data campaign ids="
            + ",".join(str(value) for value in no_data_campaigns)
        )

    return all_rows
