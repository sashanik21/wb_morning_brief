SEVERITY_LABELS = {
    "critical": "Critical",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
}
SEVERITY_ORDER = ["low", "medium", "high", "critical"]


def downgrade_severity(severity):
    severity = str(severity or "low").lower()

    if severity not in SEVERITY_ORDER:
        return "low"

    severity_index = SEVERITY_ORDER.index(severity)
    return SEVERITY_ORDER[max(severity_index - 1, 0)]


def to_number(value, default=0):
    if value in (None, ""):
        return default

    if isinstance(value, str):
        value = value.replace("%", "").replace(" ", "").replace(",", ".")

    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def calculate_problem_severity(
    metric, selected_value, past_value, dynamic_percent, abc=None
):
    selected_number = to_number(selected_value)
    past_number = to_number(past_value)
    lost_orders = 0
    lost_order_sum = 0

    if metric == "orderCount":
        lost_orders = max(past_number - selected_number, 0)
    elif metric == "orderSum":
        lost_order_sum = max(past_number - selected_number, 0)

    base = abs(to_number(dynamic_percent))

    if metric == "orderCount":
        base += lost_orders * 5
    elif metric == "orderSum":
        base += lost_order_sum / 1000
    elif metric in {"wbStocks", "realSellableStock"}:
        base += 50

    abc_multiplier = {
        "A": 1.5,
        "B": 1.2,
        "C": 1.0,
        "UNKNOWN": 0.8,
    }.get(str(abc or "UNKNOWN").upper(), 0.8)
    severity_score = round(base * abc_multiplier, 2)

    if severity_score >= 80:
        severity = "critical"
    elif severity_score >= 50:
        severity = "high"
    elif severity_score >= 25:
        severity = "medium"
    else:
        severity = "low"

    return {
        "severity": severity,
        "severityScore": severity_score,
        "lostOrders": (
            int(lost_orders)
            if float(lost_orders).is_integer()
            else round(lost_orders, 2)
        ),
        "lostOrderSum": (
            int(lost_order_sum)
            if float(lost_order_sum).is_integer()
            else round(lost_order_sum, 2)
        ),
    }


def task_priority_from_severity(severity):
    severity = str(severity or "").lower()

    if severity in {"critical", "high"}:
        return "high"
    if severity == "medium":
        return "medium"
    if severity == "low":
        return "low"

    return None
