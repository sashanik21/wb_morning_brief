import os

from app.seller_config import SELLER_CONFIG, SELLER_NAME

DEFAULT_WB_TOKEN_SECRET_NAME = "WB_API_TOKEN_TEST"
CURRENT_WB_TOKEN_SECRET_NAME = DEFAULT_WB_TOKEN_SECRET_NAME
WB_API_TOKEN = os.getenv(DEFAULT_WB_TOKEN_SECRET_NAME) or ""

HEADERS = {"Authorization": WB_API_TOKEN}

REQUIRED_SELLERS = [
    {
        "seller_name": "ООО Парадайс",
        "cabinet_name": "WB ООО Парадайс",
        "status": "active",
        "wb_api_token_env": "WB_API_TOKEN_PARADIS",
        "wb_token_secret_name": "WB_API_TOKEN_PARADIS",
    },
]



def set_wb_api_token(secret_name):
    global CURRENT_WB_TOKEN_SECRET_NAME, WB_API_TOKEN

    CURRENT_WB_TOKEN_SECRET_NAME = secret_name or DEFAULT_WB_TOKEN_SECRET_NAME
    WB_API_TOKEN = os.getenv(CURRENT_WB_TOKEN_SECRET_NAME) or ""
    HEADERS["Authorization"] = WB_API_TOKEN
    return WB_API_TOKEN


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
