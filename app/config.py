import os

from app.seller_config import SELLER_CONFIG, SELLER_NAME

WB_API_TOKEN = os.getenv("WB_API_TOKEN_TEST")

if not WB_API_TOKEN:
    raise ValueError("WB_API_TOKEN_TEST не найден в GitHub Secrets")


HEADERS = {"Authorization": WB_API_TOKEN}


ABC_RULES = {
    "A": {
        "min_open_count": 1000,
        "min_orders": 10,
        "min_order_sum": 10000,
    },
    "B": {
        "min_open_count": 500,
        "min_orders": 5,
        "min_order_sum": 5000,
    },
    "C": {
        "min_open_count": 200,
        "min_orders": 3,
        "min_order_sum": 2000,
    },
}
