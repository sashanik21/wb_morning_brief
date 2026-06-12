import html
import logging
import os

import requests

from app.constants.problem_labels import get_problem_label
from app.reports.evidence import (
    EVIDENCE_LIMIT_TELEGRAM,
    build_evidence_rows,
    escape,
    format_number,
    format_percent,
)
from app.seller_config import SELLER_NAME

TELEGRAM_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_PHOTO_API_URL = "https://api.telegram.org/bot{token}/sendPhoto"
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
                "ABC": problem.get("ABC") or "n/a",
            }

        grouped_products[group_key]["problems"].append(problem)

    return sorted(
        grouped_products.values(),
        key=lambda product: (-len(product["problems"]), product["first_index"]),
    )


def _human_readable_problem_type(problem):
    problem_label = str(problem.get("problemLabel") or "").strip()

    if problem_label:
        return problem_label

    metric = str(problem.get("metric") or "").strip()

    if metric:
        return get_problem_label(metric)

    return get_problem_label(problem.get("problemType"))


def _format_problem_line(problem):
    problem_type = html.escape(_human_readable_problem_type(problem))

    if problem.get("metric") == "wbStocks" and _is_present(
        problem.get("selectedValue")
    ):
        problem_value = html.escape(str(problem.get("selectedValue")))
    else:
        problem_value = html.escape(
            _format_dynamic_percent(problem.get("dynamicPercent"))
        )

    return f"— {problem_type}: {problem_value}"


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
    abc = html.escape(str(product.get("ABC") or "n/a"))
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
        f"Артикул WB: {nm_id}\n"
        f"ABC: {abc}\n\n"
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
        f"SKU есть в PRODUCTS: {_format_number(summary_stats.get('skuInProducts'))}\n"
        f"SKU нет в PRODUCTS: {_format_number(summary_stats.get('skuNotInProducts'))}\n"
        f"Проигнорировано ABC-фильтром: "
        f"{_format_number(summary_stats.get('skuIgnoredByAbcFilter'))}\n"
        f"Переходы в карточку: "
        f"{_format_number(summary_stats.get('totalOpenCount'))}\n"
        f"Корзины: {_format_number(summary_stats.get('totalCartCount'))}\n"
        f"Заказы: {_format_number(summary_stats.get('totalOrders'))}\n"
        f"Сумма заказов: {_format_number(summary_stats.get('totalOrderSum'))} ₽"
    )


def _format_dynamic_value(value):
    if value in (None, ""):
        return "n/a"

    try:
        number = float(value)
    except (TypeError, ValueError):
        return html.escape(str(value))

    return f"{number:.1f}%"


def _format_store_dynamic_line(
    label, selected_value, past_value, dynamic_value, suffix=""
):
    return (
        f"{label}: {_format_number(selected_value)}{suffix} vs "
        f"{_format_number(past_value)}{suffix} ({_format_dynamic_value(dynamic_value)})"
    )


def _build_store_dynamics_block(summary_stats):
    if not summary_stats:
        return ""

    return (
        "📈 <b>Динамика магазина:</b>\n"
        + _format_store_dynamic_line(
            "Переходы",
            summary_stats.get("selectedOpenCount"),
            summary_stats.get("pastOpenCount"),
            summary_stats.get("openCountDynamic"),
        )
        + "\n"
        + _format_store_dynamic_line(
            "Корзины",
            summary_stats.get("selectedCartCount"),
            summary_stats.get("pastCartCount"),
            summary_stats.get("cartCountDynamic"),
        )
        + "\n"
        + _format_store_dynamic_line(
            "Заказы",
            summary_stats.get("selectedOrderCount"),
            summary_stats.get("pastOrderCount"),
            summary_stats.get("orderCountDynamic"),
        )
        + "\n"
        + _format_store_dynamic_line(
            "Сумма заказов",
            summary_stats.get("selectedOrderSum"),
            summary_stats.get("pastOrderSum"),
            summary_stats.get("orderSumDynamic"),
            suffix=" ₽",
        )
    )


def _build_control_signals_block(summary_stats):
    if not summary_stats:
        return ""

    signals = []

    if summary_stats.get("skuNotInProducts", 0) > 0:
        signals.append("⚠️ Есть карточки WB, не внесённые в PRODUCTS")

    if summary_stats.get("skuIgnoredByAbcFilter", 0) > 0:
        signals.append("⚠️ Часть SKU проигнорирована ABC-фильтром")

    if summary_stats.get("totalOrders", 0) == 0:
        signals.append("🔴 Заказов нет")

    if summary_stats.get("totalOrderSum", 0) == 0:
        signals.append("🔴 Сумма заказов 0 ₽")

    if summary_stats.get("orderCountDynamic", 0) <= -10:
        signals.append("🔴 Заказы по магазину просели")

    if summary_stats.get("orderSumDynamic", 0) <= -10:
        signals.append("🔴 Сумма заказов по магазину просела")

    if summary_stats.get("openCountDynamic", 0) <= -15:
        signals.append("🔴 Переходы в карточки просели")

    if summary_stats.get("cartCountDynamic", 0) <= -10:
        signals.append("🔴 Корзины просели")

    if not signals:
        signals.append("✅ Критичных контрольных сигналов нет")

    return "🔎 <b>Контрольные сигналы:</b>\n" + "\n".join(signals)


def _format_drop_signal(signal):
    title = html.escape(str(signal.get("title") or "Без названия"))
    nm_id = html.escape(str(signal.get("nmId") or "n/a"))
    metric = html.escape(
        str(signal.get("problemLabel") or get_problem_label(signal.get("metric")))
    )
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


def _format_money(value):
    return f"{_format_number(value)} ₽"


def _build_ads_block(records, summary_stats):
    ads_summary = (summary_stats or {}).get("adsSummary") or {}
    ads_records = [
        record
        for record in records
        if isinstance(record, dict) and record.get("problemCategory") == "ads"
    ]

    if not ads_summary and not ads_records:
        return ""

    active_campaigns = ads_summary.get("activeCampaigns", 0)
    problem_campaigns = ads_summary.get("problemCampaigns", 0)
    block_lines = [
        "📢 <b>Реклама:</b>",
        f"Активных кампаний: <b>{_format_number(active_campaigns)}</b>",
        f"Проблемных кампаний: <b>{_format_number(problem_campaigns)}</b>",
    ]

    if not ads_records:
        block_lines.append("✅ Проблем рекламы не найдено")
        return "\n".join(block_lines)

    grouped_campaigns = {}

    for record in ads_records:
        campaign_key = record.get("campaignId") or record.get("title")

        if campaign_key not in grouped_campaigns:
            grouped_campaigns[campaign_key] = record

    problem_lines = []

    for record in list(grouped_campaigns.values())[:TELEGRAM_TOP_LIMIT]:
        title = html.escape(
            str(record.get("title") or record.get("campaignName") or "Без названия")
        )
        reason = html.escape(
            str(
                record.get("problemLabel")
                or record.get("problemType")
                or "реклама стала неэффективной"
            )
        )
        problem_lines.append(
            f"— <b>{title}</b>\n"
            f"CTR рекламы: {_format_dynamic_value(record.get('ctr'))}\n"
            f"CPC: {_format_money(record.get('cpc'))}\n"
            f"ДРР: {_format_dynamic_value(record.get('drr'))}\n"
            f"Причина:\n{reason}"
        )

    return (
        "\n".join(block_lines)
        + "\n\n🔴 <b>Проблемы рекламы:</b>\n\n"
        + "\n\n".join(problem_lines)
    )


def _insight_key(insight):
    nm_id = insight.get("nmId")

    if nm_id not in (None, ""):
        return ("nmId", str(nm_id))

    return ("title", str(insight.get("title") or "Без названия"))


def _build_root_cause_insights_block(root_cause_insights, top_products):
    if not root_cause_insights or not top_products:
        return ""

    insights_by_key = {
        _insight_key(insight): insight
        for insight in root_cause_insights
        if isinstance(insight, dict)
    }
    formatted_insights = []

    for product in top_products:
        insight = insights_by_key.get(_problem_group_key(product))

        if not insight:
            continue

        title = html.escape(
            str(insight.get("title") or product.get("title") or "Без названия")
        )
        zone = html.escape(str(insight.get("rootCauseZone") or "Недостаточно данных"))
        reason = html.escape(
            str(insight.get("reason") or "Недостаточно данных для определения причины")
        )
        checks = insight.get("whatToCheck") or []
        checks_text = html.escape(
            ", ".join(str(check) for check in checks) or "проверить карточку вручную"
        )
        formatted_insights.append(
            f"🏷️ <b>{title}</b>\n"
            f"Зона проблемы: {zone}\n"
            f"Почему: {reason}\n"
            f"Проверить: {checks_text}"
        )

    if not formatted_insights:
        return ""

    return "🧠 <b>Возможная причина:</b>\n\n" + "\n\n".join(formatted_insights)


def _format_evidence_metric(row, label, metric, suffix=""):
    selected_value = format_number(row.get(f"{metric}_selected"))
    past_value = format_number(row.get(f"{metric}_past"))
    dynamic_value = format_percent(row.get(f"{metric}_delta"))

    return f"{label}:\nсейчас {selected_value}{suffix} / было {past_value}{suffix} → {dynamic_value}"


def _telegram_evidence_conclusion(row):
    orders_delta = row.get("orderCount_delta")
    opens_delta = row.get("openCount_delta")
    carts_delta = row.get("cartCount_delta")

    if (
        orders_delta is not None
        and orders_delta < 0
        and opens_delta is not None
        and opens_delta < 0
        and carts_delta is not None
        and carts_delta < 0
    ):
        return "просадка заказов подтверждается падением переходов и корзин."

    return row.get("diagnosis") or "Требует проверки"


def _evidence_footer():
    return (
        "Формула динамики:\n"
        + "(сейчас - было) / было × 100%\n\n"
        + "Пример:\n"
        + "(17 - 30) / 30 × 100% = -43%\n\n"
        + "Источник данных:\n"
        + "WB API sales funnel, selectedPeriod vs pastPeriod."
    )


def _build_evidence_block(summary_stats):
    summary_stats = summary_stats or {}
    evidence_rows = summary_stats.get("evidenceRows")

    if evidence_rows is None:
        evidence_rows = build_evidence_rows(
            summary_stats.get("funnelData"), limit=EVIDENCE_LIMIT_TELEGRAM
        )

    logger.info("EVIDENCE BLOCK: top evidence rows: %s", len(evidence_rows))
    print("EVIDENCE BLOCK:")
    print(f"top evidence rows: {len(evidence_rows)}")

    if not evidence_rows:
        return (
            "📌 <b>Подтверждение по ключевым просадкам:</b>\n"
            "— Просадок заказов/выручки не найдено\n\n" + _evidence_footer()
        )

    formatted_rows = []

    for row in evidence_rows[:EVIDENCE_LIMIT_TELEGRAM]:
        formatted_rows.append(
            f"🏷️ <b>{escape(row.get('title') or 'Без названия')}</b>\n"
            f"Артикул продавца: {escape(row.get('vendorCode') or 'n/a')}\n"
            f"Артикул WB: {escape(row.get('nmId') or 'n/a')}\n\n"
            + _format_evidence_metric(row, "Переходы", "openCount")
            + "\n\n"
            + _format_evidence_metric(row, "Корзины", "cartCount")
            + "\n\n"
            + _format_evidence_metric(row, "Заказы", "orderCount")
            + "\n\n"
            + _format_evidence_metric(row, "Выручка", "orderSum", suffix=" ₽")
            + "\n\nВывод:\n"
            + escape(_telegram_evidence_conclusion(row))
        )

    return (
        "📌 <b>Подтверждение по ключевым просадкам:</b>\n\n"
        + "\n\n".join(formatted_rows)
        + "\n\n"
        + _evidence_footer()
    )


def _build_telegram_message(problems, summary_stats=None, root_cause_insights=None):
    records = _problems_to_records(problems)
    problem_products = _group_problems_by_product(records)
    header = _build_telegram_header(len(records), len(problem_products), summary_stats)
    message_parts = [
        header,
        _build_summary_block(summary_stats),
        _build_store_dynamics_block(summary_stats),
        _build_control_signals_block(summary_stats),
        _build_top_drop_signals_block(summary_stats),
        _build_ads_block(records, summary_stats),
        _build_evidence_block(summary_stats),
    ]

    if not records:
        message_parts.append(
            "✅ Критичных проблем не найдено\n"
            "⚠️ Это не означает отсутствие просадок — часть SKU могла быть "
            "отфильтрована ABC."
        )
        return "\n\n".join(part for part in message_parts if part)

    top_products = problem_products[:TELEGRAM_TOP_LIMIT]
    message_parts.append(
        _build_root_cause_insights_block(root_cause_insights, top_products)
    )
    formatted_products = [
        _format_product_item(index, product)
        for index, product in enumerate(top_products, start=1)
    ]
    message_parts.append(
        "🔴 <b>ТОП-5 проблемных товаров:</b>\n\n" + "\n\n".join(formatted_products)
    )

    return "\n\n".join(part for part in message_parts if part)


def _build_dashboard_caption(summary_stats=None):
    summary_stats = summary_stats or {}
    seller_name = html.escape(str(summary_stats.get("sellerName") or SELLER_NAME))

    return (
        "📊 <b>WB Morning Brief</b>\n"
        f"Продавец: <b>{seller_name}</b>\n"
        "PNG dashboard готов. Подробный текстовый бриф отправлен ниже."
    )


def _send_telegram_dashboard_image(token, chat_id, image_path, caption):
    if not image_path or not os.path.exists(image_path):
        return False

    url = TELEGRAM_PHOTO_API_URL.format(token=token)
    payload = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}

    try:
        with open(image_path, "rb") as image_file:
            response = requests.post(
                url,
                data=payload,
                files={"photo": image_file},
                timeout=TELEGRAM_TIMEOUT_SECONDS,
            )
    except OSError as error:
        logger.error("Dashboard image cannot be opened: %s", error)
        print(f"Dashboard image cannot be opened: {error}")
        return False
    except requests.RequestException as error:
        logger.error("Telegram dashboard image request failed: %s", error)
        print(f"Telegram dashboard image request failed: {error}")
        return False

    if response.status_code != 200:
        logger.error(
            "Telegram dashboard image error: status=%s text=%s",
            response.status_code,
            response.text,
        )
        print(
            "Telegram dashboard image error: "
            f"status={response.status_code} text={response.text}"
        )
        return False

    try:
        data = response.json()
    except ValueError:
        logger.error(
            "Telegram dashboard image returned invalid JSON: %s", response.text
        )
        print("Telegram dashboard image returned invalid JSON")
        return False

    if not data.get("ok"):
        logger.error("Telegram dashboard image returned error payload: %s", data)
        print(f"Telegram dashboard image returned error: {data}")
        return False

    logger.info("Telegram dashboard image sent successfully")
    print("Telegram dashboard image sent successfully")
    return True


def send_telegram_morning_brief(
    problems, summary_stats=None, dashboard_image_path=None, root_cause_insights=None
):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("Telegram credentials not configured")
        return False

    if dashboard_image_path:
        _send_telegram_dashboard_image(
            token,
            chat_id,
            dashboard_image_path,
            _build_dashboard_caption(summary_stats),
        )

    message = _build_telegram_message(
        problems,
        summary_stats=summary_stats,
        root_cause_insights=root_cause_insights,
    )
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
