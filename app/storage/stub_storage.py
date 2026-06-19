from functools import lru_cache

STUB_SELLERS = [
    {
        "seller_id": 1,
        "seller_name": "ИП Череватенко Б.С.",
        "cabinet_name": "WB ИП Череватенко Б.С.",
        "responsible": "Саша",
        "status": "active",
    }
]

STUB_PRODUCTS = [
    {
        "seller_id": 1,
        "nmId": 1088430501,
        "vendorCode": "Пэпэ Кедрус_30ml",
        "productName": "Масляные духи по мотивам Chloe Cedrus 30 мл",
        "brand": "МИЗ",
        "abc": "A",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430502,
        "vendorCode": "Пэпэ Номад_30ml",
        "productName": "Масляные духи по мотивам Chloe Nomade 30 мл",
        "brand": "МИЗ",
        "abc": "A",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430503,
        "vendorCode": "Пэпэ Лав_30ml",
        "productName": "Масляные духи по мотивам Chloe Love 30 мл",
        "brand": "МИЗ",
        "abc": "A",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430504,
        "vendorCode": "Пэпэ Флёр_30ml",
        "productName": "Масляные духи по мотивам Chloe Fleur 30 мл",
        "brand": "МИЗ",
        "abc": "B",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430505,
        "vendorCode": "Пэпэ Сигнейчур_30ml",
        "productName": "Масляные духи по мотивам Chloe Signature 30 мл",
        "brand": "МИЗ",
        "abc": "B",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430506,
        "vendorCode": "Пэпэ Розес_30ml",
        "productName": "Масляные духи по мотивам Chloe Roses 30 мл",
        "brand": "МИЗ",
        "abc": "B",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430507,
        "vendorCode": "Пэпэ Абсолю_30ml",
        "productName": "Масляные духи по мотивам Chloe Absolu 30 мл",
        "brand": "МИЗ",
        "abc": "C",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430508,
        "vendorCode": "Пэпэ Натюрель_30ml",
        "productName": "Масляные духи по мотивам Chloe Naturelle 30 мл",
        "brand": "МИЗ",
        "abc": "C",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430509,
        "vendorCode": "Пэпэ Люмьер_30ml",
        "productName": "Масляные духи по мотивам Chloe Lumineuse 30 мл",
        "brand": "МИЗ",
        "abc": "C",
        "status": "active",
    },
    {
        "seller_id": 1,
        "nmId": 1088430510,
        "vendorCode": "Пэпэ Интенс_30ml",
        "productName": "Масляные духи по мотивам Chloe Intense 30 мл",
        "brand": "МИЗ",
        "abc": "C",
        "status": "active",
    },
]

STUB_CHANGE_LOG = [
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


STUB_TASKS = []


def _copy_records(records):
    return [record.copy() for record in records]


@lru_cache(maxsize=None)
def _get_sellers_cached():
    return tuple(tuple(record.items()) for record in STUB_SELLERS)


@lru_cache(maxsize=None)
def _get_products_cached():
    return tuple(tuple(record.items()) for record in STUB_PRODUCTS)


@lru_cache(maxsize=None)
def _get_change_log_cached():
    return tuple(tuple(record.items()) for record in STUB_CHANGE_LOG)


def _records_from_cached(cached_records):
    return [dict(record) for record in cached_records]


def get_storage_status():
    return {"mode": "stub", "configured": True}


def log_storage_configuration():
    print("STORAGE MODE: stub")


def get_sellers():
    return _records_from_cached(_get_sellers_cached())


def get_products():
    return _records_from_cached(_get_products_cached())


def sync_products_from_wb_cards(seller_id, cards):
    print("STUB SYNC PRODUCTS FROM WB:")
    print(f"seller_id: {seller_id}")
    print(f"cards: {len(cards or [])}")
    print("upserted: 0")


def get_change_log():
    return _records_from_cached(_get_change_log_cached())


def create_tasks(tasks):
    STUB_TASKS.clear()
    STUB_TASKS.extend(_copy_records(tasks))
    print(f"TASKS TO CREATE: {len(tasks)}")

    for task in tasks[:5]:
        print(task)


def get_funnel_history(seller_id, nm_id, days, before_date=None):
    print(
        f"STUB FUNNEL HISTORY: seller_id={seller_id}, nm_id={nm_id}, days={days}, before_date={before_date}, rows=0"
    )
    return []


def save_funnel_snapshot(rows):
    print(f"STUB SAVE FUNNEL: {len(rows)} rows")


def save_problems(problems):
    print(f"STUB SAVE PROBLEMS: {len(problems)} rows")


def save_daily_qbiki_metrics(rows):
    print(f"STUB SAVE DAILY QBIKI METRICS: {len(rows)} rows")


def save_api_coverage_daily(rows):
    print(f"STUB SAVE API COVERAGE DAILY: {len(rows)} rows")


def get_latest_ads_metrics_by_nm_ids(seller_id, nm_ids):
    return []
