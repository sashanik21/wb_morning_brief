from datetime import datetime, timedelta

from app.collectors.cards import get_cards_list
from app.config import HEADERS
from app.wb_client import WBClient

SALES_FUNNEL_URL = (
    "https://seller-analytics-api.wildberries.ru"
    "/api/analytics/v3/sales-funnel/products"
)
MAX_FUNNEL_NM_IDS = 1000


def _format_period(start_date, end_date):
    return {
        "start": start_date.strftime("%Y-%m-%d"),
        "end": end_date.strftime("%Y-%m-%d"),
    }


def _build_sales_funnel_payload(nm_ids):
    selected_day = datetime.now().date() - timedelta(days=1)
    past_day = selected_day - timedelta(days=1)

    return {
        "selectedPeriod": _format_period(selected_day, selected_day),
        "pastPeriod": _format_period(past_day, past_day),
        "nmIds": nm_ids[:MAX_FUNNEL_NM_IDS],
        "skipDeletedNm": False,
        "limit": min(len(nm_ids), MAX_FUNNEL_NM_IDS),
        "offset": 0,
    }


def collect_sales_funnel():
    client = WBClient(HEADERS)

    cards_data = get_cards_list()

    if not cards_data:
        print("Не удалось получить карточки")
        return None

    cards = cards_data.get("cards", [])

    nm_ids = []

    for card in cards:
        nm_id = card.get("nmID")

        if nm_id:
            nm_ids.append(nm_id)

    print(f"Найдено nmIDs: {len(nm_ids)}")

    if not nm_ids:
        print("Список nmIDs пуст")
        return None

    payload = _build_sales_funnel_payload(nm_ids)

    print("Отправляем запрос в funnel API")
    print("selectedPeriod:", payload["selectedPeriod"])
    print("pastPeriod:", payload["pastPeriod"])
    print("limit:", payload["limit"])

    data = client.request(
        method="POST",
        url=SALES_FUNNEL_URL,
        json_data=payload,
    )

    return data
