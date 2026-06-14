from statistics import mean

import pandas as pd

from app.seller_config import SELLER_NAME

FORECAST_ALERT_LIMIT = 3


def _to_number(value, default=None):
    if value in (None, ""):
        return default
    try:
        return float(str(value).replace("%", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return default


def _first_present(row, keys, default=""):
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return default


def _normalize_nm_id(value):
    number = _to_number(value)
    if number is None:
        return str(value or "").strip()
    if float(number).is_integer():
        return str(int(number))
    return str(number)


def _forecast_confidence(history_count):
    if history_count >= 7:
        return "HIGH"
    if history_count >= 3:
        return "MEDIUM"
    return "LOW"


def _confidence_reason(confidence, history_count, baseline_type=None):
    if confidence != "LOW":
        return confidence
    if baseline_type in {"previous_day", "prev_day"}:
        return "LOW — baseline только по previous day"
    if history_count < 7:
        return "LOW — <7 дней истории"
    return "LOW — недостаточно заказов"


def _forecast_eta_hours(days_until_oos):
    value = _to_number(days_until_oos)
    if value is None:
        return None
    return round(value * 24, 1)


def _format_eta(days_until_oos):
    hours = _forecast_eta_hours(days_until_oos)
    if hours is None:
        return ""
    if hours < 1:
        return "сегодня"
    if hours < 24:
        return f"≈{round(hours)} ч"
    days = hours / 24
    if days <= 2:
        return f"≈{round(days)} {'день' if round(days) == 1 else 'дня'}"
    return f"≈{round(days, 1)} дня"


def _positive_values(rows, keys):
    values = []
    for row in rows:
        value = _to_number(_first_present(row, keys, default=None))
        if value is not None and value > 0:
            values.append(value)
    return values


def _average_daily_orders(current_orders, history_rows):
    order_values = _positive_values(history_rows, ["order_count", "orderCount"])
    if len(order_values) >= 7:
        return mean(order_values[:7]), "avg_7d"
    if len(order_values) >= 3:
        return mean(order_values[:3]), "avg_3d"
    previous_day = order_values[0] if order_values else _to_number(current_orders, 0)
    return previous_day, "previous_day"


def _is_strictly_decreasing(values):
    return len(values) >= 3 and all(
        values[index] > values[index + 1] for index in range(2)
    )


def _is_non_decreasing(values):
    return len(values) >= 2 and values[-1] > values[0]


def _metric_series(row, metric):
    candidates = [
        row.get(f"{metric}History"),
        row.get(f"{metric}_history"),
        row.get(f"last3{metric[0].upper()}{metric[1:]}"),
    ]
    for candidate in candidates:
        if isinstance(candidate, (list, tuple)):
            return [
                _to_number(value)
                for value in candidate
                if _to_number(value) is not None
            ]
        if isinstance(candidate, str) and "," in candidate:
            return [
                _to_number(value)
                for value in candidate.split(",")
                if _to_number(value) is not None
            ]
    current = _to_number(row.get(metric))
    previous = _to_number(row.get(f"previous{metric[0].upper()}{metric[1:]}"))
    return [value for value in [previous, current] if value is not None]


def _base_problem(
    row, problem_type, forecast_type, message, confidence, seller_id=None
):
    return {
        "sellerName": row.get("sellerName") or SELLER_NAME,
        "seller_id": seller_id,
        "nmId": _first_present(row, ["nmId", "nm_id", "nmID"]),
        "vendorCode": _first_present(row, ["vendorCode", "vendor_code"]),
        "brandName": row.get("brandName") or row.get("brand"),
        "title": row.get("title") or "Без названия",
        "ABC": row.get("ABC") or row.get("abc"),
        "problemType": problem_type,
        "problemLabel": "Прогноз риска",
        "metric": forecast_type,
        "severity": "high",
        "severityScore": 80,
        "forecastConfidence": confidence,
        "forecastType": forecast_type,
        "forecastMessage": message,
        "forecastConfidenceReason": _confidence_reason(confidence, 0),
        "recommendation": "Проверить прогнозный риск и принять превентивное действие.",
    }


def build_predictive_forecasts(
    funnel_rows, ads_rows=None, storage=None, seller_id=None
):
    if isinstance(funnel_rows, pd.DataFrame):
        funnel_records = funnel_rows.to_dict("records")
    else:
        funnel_records = list(funnel_rows or [])

    forecasts = []
    history_by_nm_id = {}

    for row in funnel_records:
        nm_id = _normalize_nm_id(_first_present(row, ["nmId", "nm_id", "nmID"]))
        history_rows = []
        if storage is not None and hasattr(storage, "get_funnel_history") and nm_id:
            history_rows = storage.get_funnel_history(seller_id, nm_id, 7)
        history_by_nm_id[nm_id] = history_rows
        confidence = _forecast_confidence(len(history_rows))

        sellable_stock = _to_number(
            _first_present(
                row, ["realSellableStock", "readyForSaleStock", "wbStocks"], default=0
            ),
            0,
        )
        avg_orders, baseline_type = _average_daily_orders(
            row.get("orderCount"), history_rows
        )
        if avg_orders and sellable_stock is not None:
            days_until_oos = round(sellable_stock / avg_orders, 1)
            if days_until_oos <= 3:
                eta = _format_eta(days_until_oos)
                message = f"⚠️ SKU может уйти в OOS {eta}"
                problem = _base_problem(
                    row, "STOCK_FORECAST", "OOS", message, confidence, seller_id
                )
                problem.update(
                    {
                        "daysUntilOOS": days_until_oos,
                        "forecastEtaHours": _forecast_eta_hours(days_until_oos),
                        "forecastConfidenceReason": _confidence_reason(
                            confidence, len(history_rows), baseline_type
                        ),
                        "selectedValue": sellable_stock,
                        "baselineType": baseline_type,
                        "baselineValue": round(avg_orders, 2),
                        "rootCause": "STOCK_FORECAST",
                        "recommendation": "Запланировать поставку или перераспределить остатки: прогноз OOS ≤ 3 дней.",
                    }
                )
                forecasts.append(problem)

        visibility = _positive_values(
            history_rows, ["visibility_score", "visibilityScore"]
        )
        organic_traffic = _positive_values(history_rows, ["open_count", "openCount"])
        positions = _positive_values(history_rows, ["avg_position", "avgPosition"])
        if visibility and organic_traffic and positions:
            current_visibility = _to_number(row.get("visibilityScore"), visibility[0])
            current_traffic = _to_number(row.get("openCount"), organic_traffic[0])
            current_position = _to_number(row.get("avgPosition"), positions[0])
            if (
                current_visibility < visibility[0]
                and current_traffic < organic_traffic[0]
                and current_position > positions[0]
            ):
                forecasts.append(
                    _base_problem(
                        row,
                        "ORGANIC_FORECAST",
                        "ORGANIC",
                        f"⚠️ nmID {nm_id} → риск падения органики",
                        confidence,
                        seller_id,
                    )
                )
                forecasts[-1]["forecastConfidenceReason"] = _confidence_reason(
                    confidence, len(history_rows)
                )

    funnel_by_nm_id = {
        _normalize_nm_id(_first_present(row, ["nmId", "nm_id", "nmID"])): row
        for row in funnel_records
    }
    for row in ads_rows or []:
        ctr_series = _metric_series(row, "ctr")
        cpc_series = _metric_series(row, "cpc")
        drr_series = _metric_series(row, "drr")
        if (
            _is_strictly_decreasing(ctr_series)
            and _is_non_decreasing(cpc_series)
            and _is_non_decreasing(drr_series)
        ):
            nm_id = _normalize_nm_id(row.get("nmId"))
            base_row = {**funnel_by_nm_id.get(nm_id, {}), **row}
            forecasts.append(
                _base_problem(
                    base_row,
                    "ADS_FORECAST",
                    "ADS",
                    f"⚠️ Реклама SKU {nm_id} становится менее эффективной",
                    _forecast_confidence(len(ctr_series)),
                    seller_id,
                )
            )
            forecasts[-1]["forecastConfidenceReason"] = _confidence_reason(
                forecasts[-1].get("forecastConfidence"), len(ctr_series)
            )

    return sorted(
        forecasts,
        key=lambda item: (
            item.get("daysUntilOOS") is None,
            item.get("daysUntilOOS") or 999,
        ),
    )
