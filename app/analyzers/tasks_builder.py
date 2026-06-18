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


def _task_dedup_key(task):
    return (
        str(task.get("date") or ""),
        str(task.get("sellerName") or ""),
        str(task.get("nmId") or ""),
        str(task.get("problemType") or ""),
        str(task.get("problemLabel") or ""),
    )


def _priority_rank(priority):
    return {
        "high": 3,
        "medium": 2,
        "low": 1,
    }.get(str(priority or "").lower(), 0)


def _choose_better_task(current_task, new_task):
    if current_task is None:
        return new_task

    current_rank = _priority_rank(current_task.get("priority"))
    new_rank = _priority_rank(new_task.get("priority"))

    if new_rank > current_rank:
        return new_task

    current_action = str(current_task.get("action") or "").strip()
    new_action = str(new_task.get("action") or "").strip()

    if not current_action and new_action:
        return new_task

    return current_task


def build_tasks_from_problems(problems):
    current_date = date.today().isoformat()
    tasks_by_key = {}

    for problem in problems or []:
        if not isinstance(problem, dict):
            continue

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

        task = {
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

        key = _task_dedup_key(task)
        tasks_by_key[key] = _choose_better_task(tasks_by_key.get(key), task)

    tasks = list(tasks_by_key.values())

    print("TASKS BUILDER:")
    print(f"input problems: {len(problems or [])}")
    print(f"unique tasks: {len(tasks)}")
    print(f"duplicates removed: {max(0, len(problems or []) - len(tasks))}")

    return tasks
