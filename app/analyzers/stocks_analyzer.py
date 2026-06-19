from datetime import datetime


def _first_present(row, keys, default=None):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _to_int(value, default=0):
    if value in (None, ""):
        return default
    try:
        return int(float(str(value).replace(",", ".")))
    except (TypeError, ValueError):
        return default


def _to_number(value):
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _nm_id(value):
    parsed = _to_int(value, None)
    return parsed if parsed is not None else None


def _stock_status(
    real_sellable_stock,
    incoming_stock,
    acceptance_stock,
    transit_stock,
    days_until_oos,
    has_data,
):
    if not has_data:
        return "нет данных по остаткам"
    if real_sellable_stock == 0:
        return "подтверждённый OOS"
    if (
        real_sellable_stock > 0
        and days_until_oos is not None
        and days_until_oos <= 3
    ):
        return "риск OOS"
    if incoming_stock > 0 or transit_stock > 0 or acceptance_stock > 0:
        return "поставка в пути"
    return "остатки подтверждены"


def _forecast_fields(nm_id, forecasts_by_nm_id):
    forecast = forecasts_by_nm_id.get(nm_id) or {}
    return {
        "days_until_oos": _to_number(
            _first_present(forecast, ["days_until_oos", "daysUntilOOS"])
        ),
        "forecast_type": _first_present(forecast, ["forecast_type", "forecastType"]),
        "forecast_message": _first_present(
            forecast, ["forecast_message", "forecastMessage", "problemLabel"]
        ),
    }


def build_stocks_daily_rows(
    funnel_rows,
    supply_stock_metrics_by_nm_id,
    predictive_forecasts=None,
    seller=None,
    seller_id=None,
    report_date=None,
):
    supply_stock_metrics_by_nm_id = supply_stock_metrics_by_nm_id or {}
    forecasts_by_nm_id = {
        _nm_id(_first_present(row, ["nm_id", "nmId", "nmID"])): row
        for row in predictive_forecasts or []
        if _nm_id(_first_present(row, ["nm_id", "nmId", "nmID"])) is not None
    }
    rows = []
    confirmed_oos = 0
    oos_risk = 0
    no_stock_data = 0
    incoming_transit = 0

    for funnel_row in funnel_rows or []:
        nm_id = _nm_id(_first_present(funnel_row, ["nm_id", "nmId", "nmID"]))
        if nm_id is None:
            continue

        supply_metrics = supply_stock_metrics_by_nm_id.get(
            str(nm_id)
        ) or supply_stock_metrics_by_nm_id.get(nm_id)
        has_data = bool(supply_metrics)
        forecast = _forecast_fields(nm_id, forecasts_by_nm_id)
        real_sellable_stock = _to_int(
            _first_present(
                supply_metrics or {}, ["real_sellable_stock", "realSellableStock"], 0
            ),
            0,
        )
        ready_for_sale_stock = _to_int(
            _first_present(
                supply_metrics or {}, ["ready_for_sale_stock", "readyForSaleStock"], 0
            ),
            0,
        )
        if real_sellable_stock == 0 and ready_for_sale_stock > 0:
            real_sellable_stock = ready_for_sale_stock
        incoming_stock = _to_int(
            _first_present(supply_metrics or {}, ["incoming_stock", "incomingStock"], 0),
            0,
        )
        returning_stock = _to_int(
            _first_present(supply_metrics or {}, ["returning_stock", "returningStock"], 0),
            0,
        )
        acceptance_stock = _to_int(
            _first_present(supply_metrics or {}, ["acceptance_stock", "acceptanceStock"], 0),
            0,
        )
        transit_stock = _to_int(
            _first_present(supply_metrics or {}, ["transit_stock", "transitStock"], 0),
            0,
        )
        status = _stock_status(
            real_sellable_stock,
            incoming_stock,
            acceptance_stock,
            transit_stock,
            forecast["days_until_oos"],
            has_data,
        )
        if status == "нет данных по остаткам":
            no_stock_data += 1
            continue
        if status == "подтверждённый OOS":
            confirmed_oos += 1
        if status == "риск OOS":
            oos_risk += 1
        if incoming_stock > 0 or transit_stock > 0 or acceptance_stock > 0:
            incoming_transit += 1

        rows.append(
            {
                "report_date": report_date,
                "seller_id": seller_id,
                "seller_name": (seller or {}).get("name")
                or (seller or {}).get("seller_name"),
                "nm_id": nm_id,
                "vendor_code": _first_present(
                    funnel_row, ["vendor_code", "vendorCode"]
                ),
                "title": _first_present(
                    funnel_row, ["title", "productName", "product_name"]
                ),
                "real_sellable_stock": real_sellable_stock,
                "incoming_stock": incoming_stock,
                "returning_stock": returning_stock,
                "ready_for_sale_stock": ready_for_sale_stock,
                "acceptance_stock": acceptance_stock,
                "transit_stock": transit_stock,
                "stock_state": status,
                "days_until_oos": forecast["days_until_oos"],
                "forecast_type": forecast["forecast_type"],
                "forecast_message": forecast["forecast_message"],
                "created_at": datetime.now().isoformat(),
            }
        )

    return rows, {
        "sku_total": len(funnel_rows or []),
        "stock_metrics_loaded": len(supply_stock_metrics_by_nm_id),
        "saved_to_stocks_daily": len(rows),
        "confirmed_oos": confirmed_oos,
        "oos_risk": oos_risk,
        "no_stock_data": no_stock_data,
        "incoming_transit": incoming_transit,
    }
