from collections import defaultdict

from app.config import HEADERS
from app.wb_client import WBClient

SUPPLIES_URL = "https://suppliers-api.wildberries.ru/api/v3/supplies"
SUPPLY_GOODS_URL = (
    "https://suppliers-api.wildberries.ru/api/v3/supplies/{supply_id}/goods"
)
SUPPLY_DETAILS_URL = "https://suppliers-api.wildberries.ru/api/v3/supplies/{supply_id}"

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

    accepted = _quantity(item, ("acceptedQuantity", "accepted", "readyForSaleQuantity"))
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

    if state in {"acceptance", "accepted", "unloading"}:
        metrics["incomingStock"] += unloading + accepted
    metrics["returningStock"] += returning
    metrics["transitStock"] += transit if state in {"in_transit", "on_the_way"} else 0


def collect_supply_stock_metrics(limit=1000):
    client = WBClient(HEADERS)
    supplies_payload = client.request("GET", f"{SUPPLIES_URL}?limit={limit}")
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

    for supply in supplies:
        if not isinstance(supply, dict):
            continue
        supply_id = supply.get("id") or supply.get("supplyId")
        if not supply_id:
            continue
        details = (
            client.request("GET", SUPPLY_DETAILS_URL.format(supply_id=supply_id)) or {}
        )
        state = _supply_state(supply, details)
        if state and state not in ACTIVE_SUPPLY_STATES:
            continue
        goods = _records(
            client.request("GET", SUPPLY_GOODS_URL.format(supply_id=supply_id))
        )
        for item in goods:
            if not isinstance(item, dict):
                continue
            nm_id = _nm_id(item)
            if nm_id:
                _merge_goods_metrics(metrics_by_nm_id[nm_id], item, state)

    return dict(metrics_by_nm_id)
