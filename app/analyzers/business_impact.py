METRIC_PRIORITY = {
    "orderSum": 100,
    "orderCount": 90,
    "cartToOrderPercent": 80,
    "cartCount": 70,
    "openCount": 60,
    "addToCartPercent": 50,
    "wbStocks": 40,
    "mpStocks": 40,
    "ads": 30,
}


def _to_number(value):
    if value in (None, ""):
        return 0

    if isinstance(value, str):
        value = value.replace("%", "").replace(" ", "").replace(",", ".")

    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def calculate_business_impact_score(problem):
    """Calculate a future ranking score for a problem record."""
    problem = problem or {}
    revenue_loss = max(
        _to_number(problem.get("potentialRevenueLoss")),
        _to_number(problem.get("lostOrderSum")),
        _to_number(problem.get("blockedRevenuePerDay")),
    )
    orders_loss = max(
        _to_number(problem.get("potentialOrdersLoss")),
        _to_number(problem.get("lostOrders")),
        _to_number(problem.get("blockedOrdersPerDay")),
    )
    metric_priority = METRIC_PRIORITY.get(str(problem.get("metric") or ""), 10)

    return (
        revenue_loss * 1000
        + orders_loss * 100
        + _to_number(problem.get("severityScore")) * 10
        + metric_priority
    )


def enrich_business_impact_scores(problems):
    """Add businessImpactScore to each problem record and print a compact summary."""
    records = problems or []

    for problem in records:
        if isinstance(problem, dict):
            problem["businessImpactScore"] = calculate_business_impact_score(problem)

    top_problem = max(
        (problem for problem in records if isinstance(problem, dict)),
        key=lambda problem: _to_number(problem.get("businessImpactScore")),
        default={},
    )

    print("BUSINESS IMPACT SCORE:")
    print(f"problems scored: {len(records)}")
    print(f"top score: {top_problem.get('businessImpactScore', 0)}")
    print(f"top nmId: {top_problem.get('nmId', '')}")
    print(f"top title: {top_problem.get('title', '')}")

    return records
