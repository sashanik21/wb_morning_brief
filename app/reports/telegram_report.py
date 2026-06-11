import html
import logging
import os

import requests

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TIMEOUT_SECONDS = 15
TELEGRAM_TOP_LIMIT = 5

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


def _format_problem_item(index, problem):
    title = html.escape(str(problem.get("title") or "Без названия"))
    problem_type = html.escape(str(problem.get("problemType") or "n/a"))
    dynamic_percent = html.escape(
        _format_dynamic_percent(problem.get("dynamicPercent"))
    )
    recommendation = html.escape(str(problem.get("recommendation") or "n/a"))

    return (
        f"<b>{index}.</b>\n"
        f"🏷️ <b>{title}</b>\n"
        f"⚠️ Тип: {problem_type}\n"
        f"📉 Динамика: {dynamic_percent}\n"
        f"💡 Рекомендация: {recommendation}"
    )


def _build_telegram_message(problems):
    records = _problems_to_records(problems)

    if not records:
        return "✅ Критичных проблем не найдено"

    top_problems = records[:TELEGRAM_TOP_LIMIT]
    formatted_problems = [
        _format_problem_item(index, problem)
        for index, problem in enumerate(top_problems, start=1)
    ]

    return (
        "📊 <b>WB Morning Brief</b>\n\n"
        f"Всего проблем: <b>{len(records)}</b>\n\n"
        "🔴 <b>ТОП-5:</b>\n\n" + "\n\n".join(formatted_problems)
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
