from app.analyzers.severity import to_number

METRIC_PRIORITY = {
    "orderSum": 0,
    "orderCount": 1,
    "cartToOrderPercent": 2,
    "openCount": 3,
    "cartCount": 4,
    "addToCartPercent": 5,
    "wbStocks": 6,
    "mpStocks": 6,
    "spend": 7,
    "drr": 8,
    "cpc": 9,
    "ctr": 10,
}


def is_below_abc_threshold(record):
    value = record.get("isBelowAbcThreshold") or record.get("is_below_abc_threshold")
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "да"}
    return bool(value)


def business_ranking_key(record, comparable_priority_exists=False):
    record = record or {}
    abc_penalty = (
        1 if is_below_abc_threshold(record) and comparable_priority_exists else 0
    )
    return (
        abc_penalty,
        -to_number(record.get("businessImpactScore")),
        -max(
            to_number(record.get("potentialRevenueLoss")),
            to_number(record.get("lostOrderSum")),
        ),
        -max(
            to_number(record.get("potentialOrdersLoss")),
            to_number(record.get("lostOrders")),
        ),
        -to_number(record.get("severityScore")),
        METRIC_PRIORITY.get(str(record.get("metric") or ""), 99),
    )


def rank_problem_records(records):
    records = [record for record in records or [] if isinstance(record, dict)]
    comparable_priority_exists = any(
        not is_below_abc_threshold(record) for record in records
    )
    return sorted(
        records,
        key=lambda record: business_ranking_key(record, comparable_priority_exists),
    )


def top_business_problem(records):
    ranked = rank_problem_records(records)
    return ranked[0] if ranked else {}


def log_business_ranking(records, source="unknown"):
    top = top_business_problem(records)
    if source != "telegram_summary":
        print("BUSINESS RANKING:")
        print(f"top nmId: {top.get('nmId', '')}")
        print(f"top title: {top.get('title', '')}")
        print(f"score: {top.get('businessImpactScore', 0)}")
        print(f"metric: {top.get('metric', '')}")
        print(f"source: {source}")
    return top
