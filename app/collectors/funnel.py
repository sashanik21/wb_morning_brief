from datetime import datetime, timedelta

from app.config import HEADERS
from app.wb_client import WBClient
from app.collectors.cards import get_cards_list


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

    url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products"

    today = datetime.now().date()

    selected_day = today - timedelta(days=1)

    past_begin = today - timedelta(days=4)
    past_end = today - timedelta(days=2)

    payload = {
        "selectedPeriod": {
            "start": selected_day.strftime("%Y-%m-%d"),
            "end": selected_day.strftime("%Y-%m-%d"),
        },
        "pastPeriod": {
            "start": past_begin.strftime("%Y-%m-%d"),
            "end": past_end.strftime("%Y-%m-%d"),
        },
        "nmIds": nm_ids,
    }

    print("Отправляем запрос в funnel API")
    print("selectedPeriod:", payload["selectedPeriod"])
    print("pastPeriod:", payload["pastPeriod"])

    data = client.request(
        method="POST",
        url=url,
        json_data=payload,
    )

    return data
