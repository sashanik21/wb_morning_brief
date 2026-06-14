from app.config import HEADERS
from app.wb_client import WBClient

CARDS_LIST_URL = "https://content-api.wildberries.ru/content/v2/get/cards/list"
CARDS_PAGE_LIMIT = 100
MAX_CARDS_PAGES = 100


def get_cards_list():
    client = WBClient(HEADERS)

    cards = []
    cursor = {"limit": CARDS_PAGE_LIMIT}

    try:
        for page in range(1, MAX_CARDS_PAGES + 1):
            payload = {
                "settings": {
                    "cursor": cursor,
                    "filter": {"withPhoto": -1},
                }
            }

            data = client.request(
                method="POST",
                url=CARDS_LIST_URL,
                json_data=payload,
            )

            if not data:
                print("Failed to collect WB cards")
                return None

            page_cards = data.get("cards", [])
            cards.extend(page_cards)

            print("CARDS COLLECTION:")
            print(f"page: {page}")
            print(f"cards received: {len(page_cards)}")
            print(f"total cards: {len(cards)}")

            if not page_cards:
                break

            response_cursor = data.get("cursor", {}) or {}
            updated_at = response_cursor.get("updatedAt")
            nm_id = response_cursor.get("nmID")

            if not updated_at or not nm_id:
                break

            cursor = {
                "limit": CARDS_PAGE_LIMIT,
                "updatedAt": updated_at,
                "nmID": nm_id,
            }
    except Exception as error:
        print("Failed to collect WB cards")
        print(error)
        return None

    if not cards:
        print("No WB cards found")

    return {"cards": cards}
