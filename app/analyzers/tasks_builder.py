from datetime import date

from app.analyzers.severity import task_priority_from_severity

HIGH_PRIORITY_PROBLEM_TYPES = (
    "orderCount",
    "orderSum",
    "wbStocks",
    "realSellableStock",
    "sellableOutOfStock",
    "acceptanceDelay",
    "returnFlow",
    "transitDelay",
    "warehouseStockZero",
)
MEDIUM_PRIORITY_PROBLEM_TYPES = (
    "openCount",
    "cartCount",
    "addToCartPercent",
    "cartToOrderPercent",
)


def _get_task_priority(problem_type):
    problem_type = str(problem_type or "")

    if any(
        priority_type in problem_type for priority_type in HIGH_PRIORITY_PROBLEM_TYPES
    ):
        return "high"

    if any(
        priority_type in problem_type for priority_type in MEDIUM_PRIORITY_PROBLEM_TYPES
    ):
        return "medium"

    return "low"


def _to_int_if_possible(value):
    if isinstance(value, bool) or value in (None, ""):
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value) if value.is_integer() else value

    if isinstance(value, str):
        stripped_value = value.strip()

        if not stripped_value:
            return value

        try:
            numeric_value = float(stripped_value.replace(",", "."))
        except ValueError:
            return value

        return int(numeric_value) if numeric_value.is_integer() else value

    return value


def build_tasks_from_problems(problems):
    current_date = date.today().isoformat()
    tasks = []

    for problem in problems:
        if problem.get("isSuppressed") or problem.get("actionPriority") == "IGNORE":
            continue

        problem_type = problem.get("problemType", "")
        problem_label = problem.get("problemLabel") or problem_type
        action_priority = problem.get("actionPriority")
        task_priority = {
            "NOW": "high",
            "TODAY": "high",
            "THIS_WEEK": "medium",
            "MONITOR": "low",
        }.get(action_priority)
        tasks.append(
            {
                "date": current_date,
                "sellerName": problem.get("sellerName", ""),
                "nmId": _to_int_if_possible(problem.get("nmId", "")),
                "vendorCode": problem.get("vendorCode", ""),
                "title": problem.get("title", ""),
                "problemType": problem_type,
                "problemLabel": problem_label,
                "priority": (
                    "low"
                    if problem.get("isBelowAbcThreshold")
                    else task_priority
                    or task_priority_from_severity(problem.get("severity"))
                    or _get_task_priority(problem_type)
                ),
                "action": problem.get("recommendation", ""),
                "status": "Новая",
            }
        )

    return tasks
