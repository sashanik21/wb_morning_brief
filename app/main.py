import json

from app.collectors.cards import get_cards_list


def main():

    print("=" * 50)
    print("GET WB CARDS")
    print("=" * 50)

    data = get_cards_list()

    if data is None:
        print("Карточки не получены")
        return

    print("КАРТОЧКИ УСПЕШНО ПОЛУЧЕНЫ")
    print("=" * 50)

    print(json.dumps(data, ensure_ascii=False, indent=2)[:10000])


if __name__ == "__main__":
    main()
