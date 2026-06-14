from collections import defaultdict

import requests

from app.config import HEADERS
from app.wb_client import WBClient

SUPPLIES_API_BASE_URL = "https://supplies-api.wildberries.ru"
SUPPLIES_URL = f"{SUPPLIES_API_BASE_URL}/api/v1/supplies"
SUPPLY_GOODS_URL = f"{SUPPLIES_API_BASE_URL}/api/v1/supplies/{{supply_id}}/goods"
SUPPLY_DETAILS_URL = f"{SUPPLIES_API_BASE_URL}/api/v3/supplies/{{supply_id}}"

ACTIVE_SUPPLY_STATES = {
    "created",
    "in_transit",
    "on_the_way",
    "delivered",
    "unloading",
    "acceptance",
    "accepted",
    "depersonalization",
    "returning",
}


def _records(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("supplies", "data", "items", "goods"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _to_int(value):
    if value in (None, ""):
        return 0
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _normalize_state(value):
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _nm_id(item):
    for key in ("nmId", "nmID", "nm_id", "barcodeNmId"):
        if item.get(key) not in (None, ""):
            return str(item.get(key))
    return ""


def _quantity(item, keys):
    return sum(_to_int(item.get(key)) for key in keys)


def _supply_state(supply, details):
    return _normalize_state(
        (details or {}).get("status")
        or (details or {}).get("state")
        or supply.get("status")
        or supply.get("state")
    )


def _merge_goods_metrics(metrics, item, state):
    if not item:
        return

    ready_for_sale = _quantity(item, ("readyForSaleQuantity", "readyForSale"))
    accepted = _quantity(item, ("acceptedQuantity", "accepted"))
    unloading = _quantity(item, ("unloadingQuantity", "unloading"))
    transit = _quantity(item, ("quantity", "planQuantity", "transitQuantity"))
    returning = _quantity(
        item, ("returningQuantity", "returnQuantity", "returnsQuantity")
    )
    depersonalized = _quantity(
        item, ("depersonalizedQuantity", "depersonalizationQuantity")
    )
    blocked = _quantity(item, ("blockedQuantity", "notForSaleQuantity"))

    if state in {"acceptance", "accepted"}:
        metrics["acceptanceStock"] += accepted or transit
    elif state == "unloading":
        metrics["acceptanceStock"] += unloading or accepted or transit
    elif state in {"returning", "return"}:
        metrics["returningStock"] += returning or transit
    elif state == "depersonalization":
        metrics["depersonalizedStock"] += depersonalized or transit
    elif state in {"blocked", "rejected"}:
        metrics["blockedStock"] += blocked or transit
    elif state in {"in_transit", "on_the_way", "created"}:
        metrics["transitStock"] += transit
    else:
        metrics["incomingStock"] += accepted + unloading + transit

    metrics["readyForSaleStock"] += ready_for_sale
    if state in {"acceptance", "accepted", "unloading"}:
        metrics["incomingStock"] += unloading + accepted
    metrics["returningStock"] += returning
    metrics["transitStock"] += transit if state in {"in_transit", "on_the_way"} else 0


def _supply_goods_request_params(supply, offset, limit):
    supply_id = supply.get("supplyID") or supply.get("supplyId")
    preorder_id = supply.get("preorderID") or supply.get("preorderId")
    if supply_id:
        return (
            supply_id,
            {"limit": limit, "offset": offset, "isPreorderID": "false"},
            "",
        )
    if preorder_id:
        return (
            preorder_id,
            {"limit": limit, "offset": offset, "isPreorderID": "true"},
            "",
        )
    return "", {}, "no supply id"


def _status_id(supply):
    return str(supply.get("statusID") or supply.get("statusId") or "").strip()


def _load_supply_goods(client, supply, limit):
    goods = []
    offset = 0
    supply_id = ""

    while True:
        supply_id, params, reason = _supply_goods_request_params(supply, offset, limit)
        if reason:
            return goods, reason

        payload = client.request(
            "GET",
            SUPPLY_GOODS_URL.format(supply_id=supply_id),
            params=params,
            silent_statuses=(404,),
        )
        if isinstance(payload, dict) and payload.get("_wb_status_code") == 404:
            return goods, "goods endpoint 404"

        page = _records(payload)
        if not page:
            return goods, "goods endpoint empty" if not goods else ""

        goods.extend(page)
        if len(page) < limit:
            return goods, ""
        offset += limit


def _log_supplies_failure(reason):
    print(f"SUPPLIES COLLECTOR WARNING: {reason}")
    print("SUPPLIES DATA: 0 rows")
    print("SUPPLIES API:")
    print("status: disabled_or_failed")
    print(f"reason: {reason}")


def collect_supply_stock_metrics(limit=1000):
    client = WBClient(HEADERS)
    payload = {"dates": [], "statusIDs": [1, 2, 3, 4, 5, 6]}
    try:
        supplies_payload = client.request("POST", SUPPLIES_URL, json_data=payload)
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as error:
        _log_supplies_failure(str(error))
        print("SUPPLIES STOCK METRICS: 0 SKU")
        return {}

    if supplies_payload is None:
        _log_supplies_failure(
            "supplies list request returned no data or unavailable status"
        )
        print("SUPPLIES STOCK METRICS: 0 SKU")
        return {}

    supplies = _records(supplies_payload)
    metrics_by_nm_id = defaultdict(
        lambda: {
            "incomingStock": 0,
            "returningStock": 0,
            "readyForSaleStock": 0,
            "acceptanceStock": 0,
            "transitStock": 0,
            "depersonalizedStock": 0,
            "blockedStock": 0,
        }
    )

    supplies_checked = 0
    goods_rows_loaded = 0
    skipped_no_id = 0
    skipped_status = 0
    goods_404_skipped = 0
    goods_endpoint_empty = 0
    no_nm_id_in_goods = 0

    for supply in supplies:
        if not isinstance(supply, dict):
            continue
        supply_id = supply.get("supplyID") or supply.get("supplyId")
        preorder_id = supply.get("preorderID") or supply.get("preorderId")
        if not supply_id and _status_id(supply) == "1":
            skipped_status += 1
            continue
        if not supply_id and not preorder_id:
            skipped_no_id += 1
            continue

        supplies_checked += 1
        try:
            details = {}
            if supply_id:
                details = (
                    client.request(
                        "GET", SUPPLY_DETAILS_URL.format(supply_id=supply_id)
                    )
                    or {}
                )
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as error:
            print(f"SUPPLIES COLLECTOR WARNING: {error}")
            continue
        state = _supply_state(supply, details)
        if state and state not in ACTIVE_SUPPLY_STATES:
            skipped_status += 1
            continue
        try:
            goods, reason = _load_supply_goods(client, supply, limit)
        except (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
        ) as error:
            print(f"SUPPLIES COLLECTOR WARNING: {error}")
            continue
        if reason == "goods endpoint 404":
            goods_404_skipped += 1
            continue
        if reason == "goods endpoint empty":
            goods_endpoint_empty += 1
        goods_rows_loaded += len(goods)
        for item in goods:
            if not isinstance(item, dict):
                continue
            nm_id = _nm_id(item)
            if nm_id:
                _merge_goods_metrics(metrics_by_nm_id[nm_id], item, state)
            else:
                no_nm_id_in_goods += 1

    metrics = dict(metrics_by_nm_id)
    print("SUPPLIES API:")
    print(f"supplies loaded: {len(supplies)}")
    print(f"stock metrics loaded: {len(metrics)}")
    print("SUPPLIES GOODS:")
    print(f"supplies checked: {supplies_checked}")
    print(f"supplies skipped no id: {skipped_no_id}")
    print(f"supplies skipped status: {skipped_status}")
    print(f"goods rows loaded: {goods_rows_loaded}")
    print(f"matched nmIds: {len(metrics)}")
    print(f"goods 404 skipped: {goods_404_skipped}")
    if goods_404_skipped:
        print("SUPPLIES GOODS WARNING:")
        print(f"goods endpoint returned 404 for {goods_404_skipped} supplies")
    if not metrics:
        if skipped_no_id:
            print(f"reason: no supply id ({skipped_no_id})")
        if goods_endpoint_empty:
            print(f"reason: goods endpoint empty ({goods_endpoint_empty})")
        if no_nm_id_in_goods:
            print(f"reason: no nmID in goods ({no_nm_id_in_goods})")
        if goods_rows_loaded and not no_nm_id_in_goods:
            print("reason: no matching nmId")
    print(f"SUPPLIES STOCK METRICS: {len(metrics)} SKU")
    return metrics
