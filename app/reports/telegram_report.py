import html
import logging
import os

import requests

from app.seller_config import SELLER_NAME

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_TOP_LIMIT = 5
TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT = 6

logger = logging.getLogger(__name__)


def _problems_to_records(problems):
    if problems is None:
        return []

    if hasattr(problems, "to_dict"):
        return problems.to_dict("records")

    if isinstance(problems, list):
        return problems

    return []


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


def _format_problem_line(problem):
    problem_type = html.escape(str(problem.get("problemType") or "n/a"))
    dynamic_percent = html.escape(
        _format_dynamic_percent(problem.get("dynamicPercent"))
    )

    return f"— {problem_type}: {dynamic_percent}"


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


def _format_product_item(index, product):
    title = html.escape(str(product["title"]))
    vendor_code = html.escape(str(product["vendorCode"]))
    nm_id = html.escape(str(product["nmId"]))
    seller_name = html.escape(str(product["sellerName"]))
    problems = product["problems"]
    problem_lines = [
        _format_problem_line(problem)
        for problem in problems[:TELEGRAM_PROBLEMS_PER_PRODUCT_LIMIT]
    ]
    recommendations = _format_recommendations(problems)

    return (
        f"<b>{index}.</b> 🏷️ <b>{title}</b>\n"
        f"Продавец: {seller_name}\n"
        f"Артикул: {vendor_code}\n"
        f"nmId: {nm_id}\n"
        f"Проблем: <b>{len(problems)}</b>\n\n"
        + "\n".join(problem_lines)
        + f"\n\n💡 <b>Что проверить:</b>\n{recommendations}"
    )


def _build_telegram_header(total_problems, problem_products_count):
    seller_name = html.escape(SELLER_NAME)

    return (
        "📊 <b>WB Morning Brief</b>\n"
        f"Продавец: <b>{seller_name}</b>\n\n"
        f"Всего проблем: <b>{total_problems}</b>\n"
        f"Проблемных товаров: <b>{problem_products_count}</b>"
    )


def _build_telegram_message(problems):
    records = _problems_to_records(problems)
    problem_products = _group_problems_by_product(records)
    header = _build_telegram_header(len(records), len(problem_products))

    if not records:
        return f"{header}\n\n✅ Критичных проблем не найдено"

    top_products = problem_products[:TELEGRAM_TOP_LIMIT]
    formatted_products = [
        _format_product_item(index, product)
        for index, product in enumerate(top_products, start=1)
    ]

    return (
        header
        + "\n\n🔴 <b>ТОП-5 проблемных товаров:</b>\n\n"
        + "\n\n".join(formatted_products)
    )


def send_telegram_morning_brief(problems):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not configured")
        return False

    message = _build_telegram_message(problems)
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
