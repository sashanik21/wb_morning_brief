import html
import logging
import os

import requests

from app.seller_config import SELLER_NAME

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_TOP_LIMIT = 5
TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT = 6
PROBLEM_TYPE_LABELS = {
    "openCount": "Падение переходов в карточку",
    "cartCount": "Падение добавлений в корзину",
    "orderCount": "Падение заказов",
    "orderSum": "Падение выручки",
    "addToCartPercent": "Падение конверсии в корзину",
    "cartToOrderPercent": "Падение конверсии в заказ",
    "wbStocks": "Закончился остаток WB",
}

logger = logging.getLogger(__name__)


def _problems_to_records(problems):
    if problems is None:
        return []

    if hasattr(problems, "to_dict"):
        return problems.to_dict("records")

    if isinstance(problems, list):
        return problems

    return []


def _is_present(value):
    return value not in (None, "") and str(value) != "nan"


def _format_dynamic_percent(value):
    if value in (None, ""):
        return "n/a"

    return f"{value}%"


def _problem_group_key(problem):
    nm_id = problem.get("nmId")

    if nm_id not in (None, ""):
        return ("nmId", str(nm_id))

    return ("title", str(problem.get("title") or "Без названия"))


def _group_problems_by_product(records):
    grouped_products = {}

    for index, problem in enumerate(records):
        group_key = _problem_group_key(problem)

        if group_key not in grouped_products:
            grouped_products[group_key] = {
                "first_index": index,
                "problems": [],
                "title": problem.get("title") or "Без названия",
                "vendorCode": problem.get("vendorCode") or "n/a",
                "nmId": problem.get("nmId") or "n/a",
                "sellerName": problem.get("sellerName") or SELLER_NAME,
            }

        grouped_products[group_key]["problems"].append(problem)

    return sorted(
        grouped_products.values(),
        key=lambda product: (-len(product["problems"]), product["first_index"]),
    )


def _human_readable_problem_type(problem):
    metric = problem.get("metric")

    if metric in PROBLEM_TYPE_LABELS:
        return PROBLEM_TYPE_LABELS[metric]

    problem_type = str(problem.get("problemType") or "").strip()

    for technical_name, human_readable_name in PROBLEM_TYPE_LABELS.items():
        if problem_type.startswith(technical_name):
            return human_readable_name

    return problem_type or "n/a"


def _format_problem_line(problem):
    problem_type = html.escape(_human_readable_problem_type(problem))
    dynamic_percent = html.escape(
        _format_dynamic_percent(problem.get("dynamicPercent"))
    )

    return f"— {problem_type}: {dynamic_percent}"


def _format_recent_changes(problems):
    changes = []
    seen_changes = set()

    for problem in problems:
        recent_changes = problem.get("recentChanges")

        if not _is_present(recent_changes):
            continue

        for change in str(recent_changes).splitlines():
            change = change.strip()

            if not change or change in seen_changes:
                continue

            seen_changes.add(change)
            changes.append(f"— {html.escape(change)}")

    if not changes:
        return ""

    return "\n\n🧩 <b>Последние изменения:</b>\n" + "\n".join(changes)


def _format_recommendations(problems):
    recommendations = []
    seen_recommendations = set()

    for problem in problems:
        recommendation = str(problem.get("recommendation") or "").strip()

        if not recommendation or recommendation in seen_recommendations:
            continue

        seen_recommendations.add(recommendation)
        recommendations.append(html.escape(recommendation))

    if not recommendations:
        return "n/a"

    return "; ".join(recommendations)


def _format_product_item(_index, product):
    title = html.escape(str(product["title"]))
    vendor_code = html.escape(str(product["vendorCode"]))
    nm_id = html.escape(str(product["nmId"]))
    problems = product["problems"]
    problem_lines = [
        _format_problem_line(problem)
        for problem in problems[:TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT]
    ]
    recommendations = _format_recommendations(problems)
    recent_changes = _format_recent_changes(problems)

    return (
        f"🏷️ <b>{title}</b>\n\n"
        f"Артикул продавца: {vendor_code}\n"
        f"Артикул WB: {nm_id}\n\n"
        f"Проблем: <b>{len(problems)}</b>\n\n"
        + "\n".join(problem_lines)
        + recent_changes
        + f"\n\n💡 <b>Что проверить:</b>\n{recommendations}"
    )


def _build_telegram_header(total_problems, problem_products_count, summary_stats=None):
    summary_stats = summary_stats or {}
    seller_name = html.escape(str(summary_stats.get("sellerName") or SELLER_NAME))

    return (
        "📊 <b>WB Morning Brief</b>\n"
        f"Продавец: <b>{seller_name}</b>\n\n"
        f"Всего проблем: <b>{total_problems}</b>\n"
        f"Проблемных товаров: <b>{problem_products_count}</b>\n"
        f"📦 SKU после ABC-фильтра: <b>{problem_products_count}</b>"
    )


def _format_number(value):
    if value in (None, ""):
        return "0"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    if number.is_integer():
        return f"{int(number):,}".replace(",", " ")

    return f"{number:,.2f}".replace(",", " ").replace(".", ",")


def _build_summary_block(summary_stats):
    if not summary_stats:
        return ""

    return (
        "📊 <b>Сводка:</b>\n"
        f"SKU из WB API: {_format_number(summary_stats.get('totalSkuFromApi'))}\n"
        f"SKU после фильтра товаров: "
        f"{_format_number(summary_stats.get('skuAfterProductsFilter'))}\n"
        f"Отфильтровано товаров: "
        f"{_format_number(summary_stats.get('skuRemovedByProductsFilter'))}\n"
        f"Проигнорировано ABC-фильтром: "
        f"{_format_number(summary_stats.get('skuIgnoredByAbcFilter'))}\n"
        f"Переходы в карточку: "
        f"{_format_number(summary_stats.get('totalOpenCount'))}\n"
        f"Корзины: {_format_number(summary_stats.get('totalCartCount'))}\n"
        f"Заказы: {_format_number(summary_stats.get('totalOrders'))}\n"
        f"Сумма заказов: {_format_number(summary_stats.get('totalOrderSum'))} ₽"
    )


def _build_control_signals_block(summary_stats):
    if not summary_stats:
        return ""

    signals = []

    if summary_stats.get("skuRemovedByProductsFilter", 0) > 0:
        signals.append("⚠️ Часть SKU исключена PRODUCTS whitelist")

    if summary_stats.get("skuIgnoredByAbcFilter", 0) > 0:
        signals.append("⚠️ Часть SKU проигнорирована ABC-фильтром")

    if summary_stats.get("totalOrders", 0) == 0:
        signals.append("🔴 Заказов нет")

    if summary_stats.get("totalOrderSum", 0) == 0:
        signals.append("🔴 Сумма заказов 0 ₽")

    if not signals:
        signals.append("✅ Критичных контрольных сигналов нет")

    return "🔎 <b>Контрольные сигналы:</b>\n" + "\n".join(signals)


def _format_drop_signal(signal):
    title = html.escape(str(signal.get("title") or "Без названия"))
    nm_id = html.escape(str(signal.get("nmId") or "n/a"))
    metric = html.escape(str(signal.get("metric") or "n/a"))
    dynamic = html.escape(_format_dynamic_percent(signal.get("dynamicPercent")))
    selected_value = html.escape(str(signal.get("selectedValue") or "0"))
    past_value = html.escape(str(signal.get("pastValue") or "0"))

    return (
        f"— {title} (WB {nm_id}): {metric} {dynamic} "
        f"({selected_value} vs {past_value})"
    )


def _build_top_drop_signals_block(summary_stats):
    if not summary_stats:
        return ""

    top_drop_signals = summary_stats.get("topDropSignals") or []

    if not top_drop_signals:
        return "📉 <b>TOP SKU по падению:</b>\n— Просадок по funnel не найдено"

    return "📉 <b>TOP SKU по падению:</b>\n" + "\n".join(
        _format_drop_signal(signal) for signal in top_drop_signals
    )


def _build_telegram_message(problems, summary_stats=None):
    records = _problems_to_records(problems)
    problem_products = _group_problems_by_product(records)
    header = _build_telegram_header(len(records), len(problem_products), summary_stats)
    message_parts = [
        header,
        _build_summary_block(summary_stats),
        _build_control_signals_block(summary_stats),
        _build_top_drop_signals_block(summary_stats),
    ]

    if not records:
        message_parts.append(
            "✅ Критичных проблем не найдено\n"
            "⚠️ Это не означает отсутствие просадок — часть SKU могла быть "
            "отфильтрована whitelist/ABC."
        )
        return "\n\n".join(part for part in message_parts if part)

    top_products = problem_products[:TELEGRAM_TOP_LIMIT]
    formatted_products = [
        _format_product_item(index, product)
        for index, product in enumerate(top_products, start=1)
    ]
    message_parts.append(
        "🔴 <b>ТОП-5 проблемных товаров:</b>\n\n" + "\n\n".join(formatted_products)
    )

    return "\n\n".join(part for part in message_parts if part)


def send_telegram_morning_brief(problems, summary_stats=None):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not configured")
        return False

    message = _build_telegram_message(problems, summary_stats=summary_stats)
    url = TELEGRAM_API_URL.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=TELEGRAM_TIMEOUT_SECONDS,
        )
    except requests.RequestException as error:
        logger.error("Telegram API request failed: %s", error)
        print(f"Telegram API request failed: {error}")
        return False

    if response.status_code != 200:
        logger.error(
            "Telegram API error: status=%s text=%s",
            response.status_code,
            response.text,
        )
        print(f"Telegram API error: status={response.status_code} text={response.text}")
        return False

    try:
        data = response.json()
    except ValueError:
        logger.error("Telegram API returned invalid JSON: %s", response.text)
        print("Telegram API returned invalid JSON")
        return False

    if not data.get("ok"):
        logger.error("Telegram API returned error payload: %s", data)
        print(f"Telegram API returned error: {data}")
        return False

    logger.info("Telegram Morning Brief sent successfully")
    print("Telegram Morning Brief sent successfully")
    return True
