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
    return [
        {
            "seller_id": 1,
            "nmId": 1088430501,
            "vendorCode": "Пэпэ Кедрус_30ml Шнейе?",
            "productName": "Масляные духи по мотивам Chloe Cedrus 30 мл",
            "brand": "МИЗ",
            "abc": "A",
        },
        {
            "seller_id": 1,
            "nmId": 1088430502,
            "vendorCode": "Пэпэ Номад_30ml",
            "productName": "Масляные духи по мотивам Chloe Nomade 30 мл",
            "brand": "МИЗ",
            "abc": "A",
        },
        {
            "seller_id": 1,
            "nmId": 1088430503,
            "vendorCode": "Пэпэ Лав_30ml",
            "productName": "Масляные духи по мотивам Chloe Love 30 мл",
            "brand": "МИЗ",
            "abc": "A",
        },
        {
            "seller_id": 1,
            "nmId": 1088430504,
            "vendorCode": "Пэпэ Флёр_30ml",
            "productName": "Масляные духи по мотивам Chloe Fleur 30 мл",
            "brand": "МИЗ",
            "abc": "B",
        },
        {
            "seller_id": 1,
            "nmId": 1088430505,
            "vendorCode": "Пэпэ Сигнейчур_30ml",
            "productName": "Масляные духи по мотивам Chloe Signature 30 мл",
            "brand": "МИЗ",
            "abc": "B",
        },
        {
            "seller_id": 1,
            "nmId": 1088430506,
            "vendorCode": "Пэпэ Розес_30ml",
            "productName": "Масляные духи по мотивам Chloe Roses 30 мл",
            "brand": "МИЗ",
            "abc": "B",
        },
        {
            "seller_id": 1,
            "nmId": 1088430507,
            "vendorCode": "Пэпэ Абсолю_30ml",
            "productName": "Масляные духи по мотивам Chloe Absolu 30 мл",
            "brand": "МИЗ",
            "abc": "B",
        },
        {
            "seller_id": 1,
            "nmId": 1088430508,
            "vendorCode": "Пэпэ Натюрель_30ml",
            "productName": "Масляные духи по мотивам Chloe Naturelle 30 мл",
            "brand": "МИЗ",
            "abc": "C",
        },
        {
            "seller_id": 1,
            "nmId": 1088430509,
            "vendorCode": "Пэпэ Люмьер_30ml",
            "productName": "Масляные духи по мотивам Chloe Lumineuse 30 мл",
            "brand": "МИЗ",
            "abc": "C",
        },
        {
            "seller_id": 1,
            "nmId": 1088430510,
            "vendorCode": "Пэпэ Интенс_30ml",
            "productName": "Масляные духи по мотивам Chloe Intense 30 мл",
            "brand": "МИЗ",
            "abc": "C",
        },
    ]


def get_change_log():
    return [
        {
            "date": "2026-06-10",
            "seller_id": 1,
            "nmId": 1088430501,
            "changeType": "Главное фото",
            "oldValue": "старое фото",
            "newValue": "новое фото",
            "comment": "Изменили главное фото карточки",
        },
        {
            "date": "2026-06-09",
            "seller_id": 1,
            "nmId": 1088430502,
            "changeType": "Цена",
            "oldValue": "1290",
            "newValue": "1190",
            "comment": "Снизили цену для теста конверсии",
        },
        {
            "date": "2026-06-08",
            "seller_id": 1,
            "nmId": 1088430503,
            "changeType": "Ставка рекламы",
            "oldValue": "350",
            "newValue": "420",
            "comment": "Повысили ставку рекламы в поиске",
        },
        {
            "date": "2026-06-07",
            "seller_id": 1,
            "nmId": 1088430504,
            "changeType": "Описание / SEO",
            "oldValue": "старое описание",
            "newValue": "обновлённое описание",
            "comment": "Обновили описание и SEO-ключи карточки",
        },
        {
            "date": "2026-06-06",
            "seller_id": 1,
            "nmId": 1088430505,
            "changeType": "Остатки / поставка",
            "oldValue": "15",
            "newValue": "95",
            "comment": "Добавили поставку на склад WB",
        },
    ]


def create_tasks(tasks):
    print(f"TASKS TO CREATE: {len(tasks)}")
