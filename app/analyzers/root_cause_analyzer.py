from app.constants.problem_labels import get_problem_label


def _is_missing(value):
    if value is None:
        return True

    if isinstance(value, str) and value == "":
        return True

    return False


def _get_nested_value(data, path, default=None):
    if not isinstance(data, dict):
        return default

    if path in data:
        return data[path]

    current = data

    for key in path.split("."):
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current


def _first_present(data, paths, default=None):
    for path in paths:
        value = _get_nested_value(data, path, default=None)

        if not _is_missing(value):
            return value

    return default


def _extract_products(funnel_data):
    if isinstance(funnel_data, dict):
        products = _get_nested_value(funnel_data, "data.products")

        if isinstance(products, list):
            return products

        products = funnel_data.get("products")

        if isinstance(products, list):
            return products

        products = funnel_data.get("data")

        if isinstance(products, list):
            return products

    if isinstance(funnel_data, list):
        return funnel_data

    return []


def _to_number(value):
    if _is_missing(value):
        return None

    if isinstance(value, (int, float)):
        return value

    if isinstance(value, str):
        normalized = value.replace("%", "").replace(" ", "").replace(",", ".")

        try:
            return float(normalized)
        except ValueError:
            return None

    return None


def _calculate_dynamic_percent(selected_value, past_value):
    selected_number = _to_number(selected_value)
    past_number = _to_number(past_value)

    if selected_number is None or past_number in (None, 0):
        return None

    return ((selected_number - past_number) / past_number) * 100


def _metric_paths(period, metric):
    conversion_paths = []

    if metric in {"addToCartPercent", "cartToOrderPercent"}:
        conversion_paths = [
            f"statistic.{period}.conversions.{metric}",
            f"{period}.conversions.{metric}",
            f"statistics.{period}.conversions.{metric}",
        ]

        if metric == "addToCartPercent":
            conversion_paths.extend(
                [
                    f"statistic.{period}.addToCartConversion",
                    f"{period}.addToCartConversion",
                ]
            )
        else:
            conversion_paths.extend(
                [
                    f"statistic.{period}.cartToOrderConversion",
                    f"{period}.cartToOrderConversion",
                ]
            )

    return [
        *conversion_paths,
        f"statistic.{period}.{metric}",
        f"statistics.{period}.{metric}",
        f"{period}.{metric}",
        f"{metric}.{period}",
    ]


INSUFFICIENT_DATA_ZONE = "Недостаточно данных"
INSUFFICIENT_DATA_REASON = "Недостаточно данных для определения причины"
INSUFFICIENT_DATA_CHECKS = ["проверить карточку вручную"]
INSUFFICIENT_HISTORY_ZONE = "INSUFFICIENT_HISTORY"
INSUFFICIENT_HISTORY_REASON = "Недостаточно истории для сравнения рекламных метрик"
INSUFFICIENT_HISTORY_CHECKS = [
    "накопить рекламную историю",
    "проверить новую рекламную активность",
]

ADS_ROOT_CAUSE_RULE = {
    "zone": "Реклама",
    "reason": "Рекламные метрики просели или реклама стала неэффективной",
    "checks": [
        "CTR рекламной кампании",
        "CPC и CPM",
        "ДРР",
        "дневной бюджет и статус кампании",
    ],
}

ROOT_CAUSE_RULES = [
    {
        "zone": "Остатки WB",
        "reason": "Товар отсутствует в sellable stock",
        "checks": [
            "поставку",
            "распределение по складам",
            "наличие на ключевых складах",
        ],
    },
    {
        "zone": "Верх воронки / трафик",
        "reason": "Заказы упали вместе с переходами в карточку",
        "checks": [
            "позиции в поиске",
            "рекламные показы",
            "CTR карточки",
            "главное фото",
            "ставки рекламы",
        ],
    },
    {
        "zone": "Карточка / цена / УТП",
        "reason": "Переходы сохранились, но покупатели хуже добавляют товар в корзину",
        "checks": [
            "цену",
            "скидку",
            "главное фото",
            "отзывы",
            "инфографику",
            "конкурентов",
        ],
    },
    {
        "zone": "Доставка / остатки / цена на этапе заказа",
        "reason": "Корзины есть, но хуже конвертируются в заказ",
        "checks": [
            "сроки доставки",
            "остатки по складам",
            "цену",
            "наличие на ключевых складах",
            "конкурентов",
        ],
    },
    {
        "zone": "Средний чек / цена",
        "reason": "Количество заказов не просело, но сумма заказов снизилась",
        "checks": [
            "цену продажи",
            "скидки",
            "структуру заказов",
            "размер/объём товара",
        ],
    },
]


def _records(data):
    if data is None:
        return []

    if hasattr(data, "to_dict"):
        return data.to_dict("records")

    if isinstance(data, list):
        return data

    return []


def _normalize_nm_id(nm_id):
    if nm_id in (None, ""):
        return ""

    number = _to_number(nm_id)

    if number is not None:
        if isinstance(number, float) and number.is_integer():
            return str(int(number))

        return str(number)

    return str(nm_id).strip()


def _product_key(record):
    nm_id = _normalize_nm_id(record.get("nmId"))

    if nm_id:
        return ("nmId", nm_id)

    return ("title", str(record.get("title") or ""))


def _group_problem_records(problems):
    grouped = {}

    for problem in _records(problems):
        if not isinstance(problem, dict):
            continue

        key = _product_key(problem)

        if key not in grouped:
            grouped[key] = {
                "problems": [],
                "nmId": problem.get("nmId"),
                "vendorCode": problem.get("vendorCode"),
                "title": problem.get("title"),
            }

        grouped[key]["problems"].append(problem)

    return grouped


def _funnel_record_product_value(record, key):
    product = record.get("product", {}) if isinstance(record, dict) else {}

    if key in record:
        return record.get(key)

    if isinstance(product, dict) and key in product:
        return product.get(key)

    return None


def _flatten_funnel_records(funnel_rows):
    rows = _records(funnel_rows)

    if rows:
        return [row for row in rows if isinstance(row, dict)]

    return [row for row in _extract_products(funnel_rows) if isinstance(row, dict)]


def _build_funnel_records_by_key(funnel_rows):
    records_by_key = {}

    for record in _flatten_funnel_records(funnel_rows):
        nm_id = _normalize_nm_id(_funnel_record_product_value(record, "nmId"))
        title = _funnel_record_product_value(record, "title")
        key = ("nmId", nm_id) if nm_id else ("title", str(title or ""))

        if key[1]:
            records_by_key[key] = record

    return records_by_key


def _problem_metric(problem):
    return str(problem.get("metric") or problem.get("problemType") or "")


def _problem_dynamic(problem):
    return _to_number(problem.get("dynamicPercent"))


def _metric_is_falling_from_problem(problem):
    dynamic = _problem_dynamic(problem)

    if dynamic is not None:
        return dynamic < 0

    return "падение" in str(problem.get("problemType") or "").lower()


def _metric_is_falling_from_funnel(record, metric):
    selected_value = _first_present(
        record, _metric_paths("selected", metric), default=None
    )
    past_value = _first_present(record, _metric_paths("past", metric), default=None)
    dynamic = _calculate_dynamic_percent(selected_value, past_value)

    return dynamic is not None and dynamic < 0


def _metric_is_falling(metric, product_problems, funnel_record):
    for problem in product_problems:
        if _problem_metric(problem) == metric and _metric_is_falling_from_problem(
            problem
        ):
            return True

    if funnel_record:
        return _metric_is_falling_from_funnel(funnel_record, metric)

    return False


def _has_ads_problem(product_problems):
    ads_problem_types = {
        "ads_ctr_drop",
        "ads_cpc_growth",
        "ads_cpm_growth",
        "ads_drr_growth",
        "ads_spend_without_orders",
        "ads_ctr_low",
        "ads_ineffective",
        "ads_stopped",
        "ads_impressions_drop",
        "ads_traffic_drop",
        "ads_reach_expensive",
    }

    for problem in product_problems:
        if problem.get("problemCategory") == "ads":
            return True

        if problem.get("problemType") in ads_problem_types:
            return True

    return False


def _main_ads_problem(product_problems):
    for problem in product_problems:
        if problem.get("problemCategory") == "ads" or str(
            problem.get("problemType") or ""
        ).startswith("ads_"):
            return str(
                problem.get("problemLabel")
                or problem.get("problemType")
                or "Проблема рекламы"
            )

    return "Проблема рекламы"


def _wb_stock_is_zero(product_problems, funnel_record):
    for problem in product_problems:
        if _problem_metric(problem) not in {"wbStocks", "realSellableStock"}:
            continue

        selected_value = _to_number(problem.get("selectedValue"))

        if selected_value == 0:
            return True

    if not funnel_record:
        return False

    stock_value = _to_number(
        _first_present(
            funnel_record,
            [
                "realSellableStock",
                "product.stocks.realSellable",
                "product.stocks.wb",
                "stocks.wb",
                "wbStocks",
            ],
            default=None,
        )
    )

    return stock_value == 0


def _has_insufficient_history_problem(product_problems):
    return any(
        problem.get("rootCause") == "INSUFFICIENT_HISTORY"
        or problem.get("baselineReliability") == "INSUFFICIENT_HISTORY"
        for problem in product_problems
    )


def _main_problem(product_problems, preferred_metric):
    for problem in product_problems:
        if _problem_metric(problem) == preferred_metric:
            return str(
                problem.get("problemLabel") or get_problem_label(preferred_metric)
            )

    return get_problem_label(preferred_metric)


def _build_insight(product, zone, reason, checks, main_problem):
    return {
        "nmId": product.get("nmId") or "",
        "vendorCode": product.get("vendorCode") or "",
        "title": product.get("title") or "Без названия",
        "mainProblem": main_problem,
        "rootCauseZone": zone,
        "reason": reason,
        "whatToCheck": checks,
    }


def analyze_root_causes(problems, funnel_rows):
    grouped_problems = _group_problem_records(problems)
    funnel_records_by_key = _build_funnel_records_by_key(funnel_rows)
    insights = []

    for key, product in grouped_problems.items():
        product_problems = product["problems"]
        funnel_record = funnel_records_by_key.get(key)

        if funnel_record:
            product["nmId"] = product.get("nmId") or _funnel_record_product_value(
                funnel_record, "nmId"
            )
            product["vendorCode"] = product.get(
                "vendorCode"
            ) or _funnel_record_product_value(funnel_record, "vendorCode")
            product["title"] = product.get("title") or _funnel_record_product_value(
                funnel_record, "title"
            )

        order_count_falls = _metric_is_falling(
            "orderCount", product_problems, funnel_record
        )
        open_count_falls = _metric_is_falling(
            "openCount", product_problems, funnel_record
        )
        cart_count_falls = _metric_is_falling(
            "cartCount", product_problems, funnel_record
        )
        cart_to_order_falls = _metric_is_falling(
            "cartToOrderPercent", product_problems, funnel_record
        )
        order_sum_falls = _metric_is_falling(
            "orderSum", product_problems, funnel_record
        )

        if _has_insufficient_history_problem(product_problems):
            insights.append(
                _build_insight(
                    product,
                    INSUFFICIENT_HISTORY_ZONE,
                    INSUFFICIENT_HISTORY_REASON,
                    INSUFFICIENT_HISTORY_CHECKS,
                    product_problems[0].get("problemLabel")
                    or "новая рекламная активность",
                )
            )
        elif _has_ads_problem(product_problems):
            insights.append(
                _build_insight(
                    product,
                    ADS_ROOT_CAUSE_RULE["zone"],
                    ADS_ROOT_CAUSE_RULE["reason"],
                    ADS_ROOT_CAUSE_RULE["checks"],
                    _main_ads_problem(product_problems),
                )
            )
        elif _wb_stock_is_zero(product_problems, funnel_record):
            rule = ROOT_CAUSE_RULES[0]
            insights.append(
                _build_insight(
                    product,
                    rule["zone"],
                    product_problems[0].get("rootCause") or rule["reason"],
                    rule["checks"],
                    _main_problem(product_problems, "realSellableStock"),
                )
            )
        elif order_count_falls and open_count_falls:
            rule = ROOT_CAUSE_RULES[1]
            insights.append(
                _build_insight(
                    product,
                    rule["zone"],
                    rule["reason"],
                    rule["checks"],
                    _main_problem(product_problems, "orderCount"),
                )
            )
        elif order_count_falls and not open_count_falls and cart_count_falls:
            rule = ROOT_CAUSE_RULES[2]
            insights.append(
                _build_insight(
                    product,
                    rule["zone"],
                    rule["reason"],
                    rule["checks"],
                    _main_problem(product_problems, "orderCount"),
                )
            )
        elif order_count_falls and not cart_count_falls and cart_to_order_falls:
            rule = ROOT_CAUSE_RULES[3]
            insights.append(
                _build_insight(
                    product,
                    rule["zone"],
                    rule["reason"],
                    rule["checks"],
                    _main_problem(product_problems, "orderCount"),
                )
            )
        elif order_sum_falls and not order_count_falls:
            rule = ROOT_CAUSE_RULES[4]
            insights.append(
                _build_insight(
                    product,
                    rule["zone"],
                    rule["reason"],
                    rule["checks"],
                    _main_problem(product_problems, "orderSum"),
                )
            )
        else:
            insights.append(
                _build_insight(
                    product,
                    INSUFFICIENT_DATA_ZONE,
                    INSUFFICIENT_DATA_REASON,
                    INSUFFICIENT_DATA_CHECKS,
                    str(
                        product_problems[0].get("problemLabel")
                        or get_problem_label(product_problems[0].get("metric"))
                    ),
                )
            )

    return insights
