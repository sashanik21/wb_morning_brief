import os
from datetime import datetime, timedelta

import requests

import app.config as wb_config
from app.collectors.ads import _ads_error_summary, _load_campaigns
from app.storage.supabase_storage import _get_client

ADS_NORMQUERY_STATS_URL = "https://advert-api.wildberries.ru/adv/v0/normquery/stats"
ADS_AUTO_STAT_WORDS_URL = "https://advert-api.wildberries.ru/adv/v2/auto/stat-words"
ADS_CLUSTERS_TIMEOUT_SECONDS = 60

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


def _extract_cluster_rows(payload, campaign):
    rows = []
    campaign_id = campaign.get("campaign_id")

    for item in _iter_dicts(payload):
        cluster = _cluster_value(item)
        if cluster in (None, ""):
            continue

        spend = _first_present(item, ["sum", "spend", "expense", "expenses", "cost"])
        cart_count = _first_present(item, ["atbs", "cart_count", "cartCount", "carts"])
        orders_count = _first_present(item, ["orders", "orders_count", "ordersCount"])

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
                "orders_count": _to_int(orders_count),
                "cpo_cart": _safe_cpo(spend, cart_count),
                "cpo_order": _safe_cpo(spend, orders_count),
                "raw_json": item,
            }
        )

    return rows


def _request_normquery_stats(token, campaign_id, report_date):
    payload = {
        "advertId": _to_int(campaign_id),
        "from": str(report_date),
        "to": str(report_date),
    }
    response = requests.post(
        ADS_NORMQUERY_STATS_URL,
        headers={"Authorization": token},
        json=payload,
        timeout=ADS_CLUSTERS_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        _summary_log(
            f"ADS CLUSTERS: {_ads_error_summary(response)} campaign_id={campaign_id}"
        )
        _debug_log("ADS CLUSTERS TEXT:", response.text)
        return None
    try:
        return response.json()
    except ValueError:
        _summary_log(f"ADS CLUSTERS: invalid JSON campaign_id={campaign_id}")
        return None


def _request_auto_stat_words(token, campaign_id):
    response = requests.get(
        ADS_AUTO_STAT_WORDS_URL,
        headers={"Authorization": token},
        params={"id": campaign_id},
        timeout=ADS_CLUSTERS_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        _summary_log(
            f"ADS CLUSTERS: {_ads_error_summary(response)} campaign_id={campaign_id}"
        )
        _debug_log("ADS CLUSTERS TEXT:", response.text)
        return None
    try:
        return response.json()
    except ValueError:
        _summary_log(f"ADS CLUSTERS: invalid JSON campaign_id={campaign_id}")
        return None


def _request_campaign_clusters(token, campaign, report_date):
    campaign_id = campaign.get("campaign_id")
    payload = _request_normquery_stats(token, campaign_id, report_date)
    rows = _extract_cluster_rows(payload, campaign) if payload is not None else []
    if rows:
        return rows

    payload = _request_auto_stat_words(token, campaign_id)
    return _extract_cluster_rows(payload, campaign) if payload is not None else []


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
    if campaigns is None:
        campaigns, _ = _load_campaigns(token, seller_id)

    campaigns = campaigns or []
    _summary_log(f"ADS CLUSTERS: campaigns found={len(campaigns)}")

    processed = 0
    total_clusters = 0
    saved_rows = 0
    no_data_campaigns = []
    all_rows = []

    for campaign in campaigns:
        campaign_id = campaign.get("campaign_id")
        if campaign_id in (None, ""):
            continue
        processed += 1
        try:
            rows = _request_campaign_clusters(token, campaign, report_date)
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

        if not rows:
            no_data_campaigns.append(campaign_id)
            _summary_log(f"ADS CLUSTERS: no data for campaign_id={campaign_id}")
            continue

        total_clusters += len(rows)
        try:
            saved_rows += _save_ads_clusters(rows, report_date, seller_id, seller_name)
        except Exception as error:
            _summary_log(
                f"ADS CLUSTERS: Supabase save error campaign_id={campaign_id} "
                f"error={error}"
            )
        all_rows.extend(rows)

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
