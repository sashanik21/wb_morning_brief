from app.config import HEADERS
from app.wb_client import WBClient


def get_cards_list():

    client = WBClient(HEADERS)

    url = "https://content-api.wildberries.ru/content/v2/get/cards/list"

    payload = {"settings": {"cursor": {"limit": 10}, "filter": {"withPhoto": -1}}}

    data = client.request(method="POST", url=url, json_data=payload)

    return data
