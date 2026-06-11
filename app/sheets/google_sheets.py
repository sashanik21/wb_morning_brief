def get_sellers():
    return [
        {
            "seller_id": 1,
            "seller_name": "ИП Череватенко Б.С.",
            "cabinet_name": "WB ИП Череватенко Б.С.",
            "responsible": "Саша",
            "status": "active",
        }
    ]


def get_products():
    return []


def get_change_log():
    return []


def create_tasks(tasks):
    print(f"TASKS TO CREATE: {len(tasks)}")
