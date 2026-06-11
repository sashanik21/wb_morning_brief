from datetime import datetime, timedelta

from app.config import HEADERS
from app.wb_client import WBClient


def collect_sales_funnel():
    client = WBClient(HEADERS)

    url = "https://seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products/history"

    today = datetime.now().date()
    date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")

    payload = {
        "period": {
            "begin": date_from,
            "end": date_to,
        },
        "timezone": "Europe/Moscow",
        "aggregationLevel": "day",
    }

    return client.request(
        method="POST",
        url=url,
        json_data=payload,
    )
