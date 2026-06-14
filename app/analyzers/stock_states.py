STOCK_STATES = {
    "SELLABLE",
    "IN_ACCEPTANCE",
    "IN_TRANSIT",
    "RETURNING",
    "DEPERSONALIZED",
    "IN_LOGISTICS",
    "BLOCKED",
}

STOCK_RISK_TYPES = {
    "sellableOutOfStock",
    "acceptanceDelay",
    "returnFlow",
    "transitDelay",
}

TEMPORARILY_UNAVAILABLE_LABEL = "⚠️ SKU временно недоступен для продажи"
TEMPORARILY_UNAVAILABLE_REASON = "Товар уже находится в логистике WB"
NO_VISIBLE_SUPPLY_REASON = (
    "Товар отсутствует в sellable stock. "
    "Данных о поставке или возврате в логистике WB нет."
)
SUPPLY_READY_MISMATCH_REASON = (
    "Товар есть в supply goods как readyForSale, но не отражается в sellable stock. "
    "Проверить расхождение WB stocks vs supplies."
)


def to_number(value, default=0):
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace("%", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def _metric(record, key):
    return to_number(record.get(key), 0)


def enrich_stock_metrics(record, supply_metrics=None):
    supply_metrics = supply_metrics or {}
    wb_stocks = _metric(record, "wbStocks")
    real_sellable_stock = _metric(record, "realSellableStock")
    if real_sellable_stock == 0:
        real_sellable_stock = wb_stocks
    supply_ready_for_sale = to_number(supply_metrics.get("readyForSaleStock"), 0)
    ready_for_sale = _metric(record, "readyForSaleStock") + supply_ready_for_sale
    incoming = _metric(record, "incomingStock") + to_number(
        supply_metrics.get("incomingStock"), 0
    )
    returning = _metric(record, "returningStock") + to_number(
        supply_metrics.get("returningStock"), 0
    )
    acceptance = _metric(record, "acceptanceStock") + to_number(
        supply_metrics.get("acceptanceStock"), 0
    )
    transit = _metric(record, "transitStock") + to_number(
        supply_metrics.get("transitStock"), 0
    )
    depersonalized = _metric(record, "depersonalizedStock") + to_number(
        supply_metrics.get("depersonalizedStock"), 0
    )
    blocked = _metric(record, "blockedStock") + to_number(
        supply_metrics.get("blockedStock"), 0
    )

    if real_sellable_stock > 0:
        stock_state = "SELLABLE"
        risk_type = ""
    elif incoming > 0 or acceptance > 0 or transit > 0:
        stock_state = "IN_LOGISTICS"
        risk_type = (
            "acceptanceDelay" if incoming > 0 or acceptance > 0 else "transitDelay"
        )
    elif returning > 0:
        stock_state = "RETURNING"
        risk_type = "returnFlow"
    elif depersonalized > 0:
        stock_state = "DEPERSONALIZED"
        risk_type = "sellableOutOfStock"
    elif blocked > 0:
        stock_state = "BLOCKED"
        risk_type = "sellableOutOfStock"
    else:
        stock_state = "BLOCKED" if wb_stocks == 0 else "SELLABLE"
        risk_type = "sellableOutOfStock" if wb_stocks == 0 else ""

    return {
        "realSellableStock": real_sellable_stock,
        "incomingStock": incoming,
        "returningStock": returning,
        "readyForSaleStock": ready_for_sale,
        "acceptanceStock": acceptance,
        "transitStock": transit,
        "stockState": stock_state,
        "stockRiskType": risk_type,
    }


def has_wb_logistics_stock(stock_metrics):
    return any(
        to_number(stock_metrics.get(key), 0) > 0
        for key in (
            "incomingStock",
            "acceptanceStock",
            "transitStock",
        )
    )


def has_supply_ready_mismatch(stock_metrics):
    return (
        to_number(stock_metrics.get("readyForSaleStock"), 0) > 0
        and to_number(stock_metrics.get("realSellableStock"), 0) == 0
    )


def stock_root_cause(stock_metrics):
    state = stock_metrics.get("stockState")
    if has_supply_ready_mismatch(stock_metrics):
        return "Расхождение между Supplies и Funnel stocks"
    if state == "BLOCKED" and not has_wb_logistics_stock(stock_metrics):
        return "Нет остатков и нет видимой поставки/возврата"
    if state == "IN_LOGISTICS":
        return "Товар в логистике WB, но ещё не доступен к продаже"
    if state == "IN_ACCEPTANCE":
        return (
            "Товар отсутствует в sellable stock, но партия уже находится в приёмке WB"
        )
    if state == "RETURNING":
        return (
            "Товар отсутствует в sellable stock, но уже едет возвратами в логистике WB"
        )
    if state == "IN_TRANSIT":
        return (
            "Товар отсутствует в sellable stock, но партия уже находится в транзите WB"
        )
    return "Товар отсутствует в sellable stock"
