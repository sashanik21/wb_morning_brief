PROBLEM_LABELS = {
    "openCount": "Переходы в карточку",
    "cartCount": "Добавления в корзину",
    "orderCount": "Заказы",
    "orderSum": "Выручка",
    "addToCartPercent": "Конверсия в корзину",
    "cartToOrderPercent": "Конверсия в заказ",
    "wbStocks": "Остатки WB",
    "realSellableStock": "Sellable stock",
    "STOCK_FORECAST": "Прогноз OOS",
    "ADS_FORECAST": "Прогноз эффективности рекламы",
    "ORGANIC_FORECAST": "Прогноз органики",
    "OOS": "Прогноз OOS",
    "ADS": "Прогноз рекламы",
    "ORGANIC": "Прогноз органики",
}


def get_problem_label(metric):
    metric = str(metric or "").strip()

    if metric in PROBLEM_LABELS:
        return PROBLEM_LABELS[metric]

    for technical_name, problem_label in PROBLEM_LABELS.items():
        if metric.startswith(technical_name):
            return problem_label

    return metric or "n/a"
