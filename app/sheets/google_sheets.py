import json
import os
from functools import lru_cache

import gspread
from google.oauth2.service_account import Credentials

GOOGLE_SHEETS_CREDENTIALS_JSON_ENV = "GOOGLE_SHEETS_CREDENTIALS_JSON"
GOOGLE_SHEETS_SPREADSHEET_ID_ENV = "GOOGLE_SHEETS_SPREADSHEET_ID"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]

SELLERS_WORKSHEET = "SELLERS"
PRODUCTS_WORKSHEET = "PRODUCTS"
CHANGE_LOG_WORKSHEET = "CHANGE_LOG"

SELLER_COLUMNS = [
    "seller_id",
    "seller_name",
    "cabinet_name",
    "responsible",
    "status",
]
PRODUCT_COLUMNS = [
    "seller_id",
    "nmId",
    "vendorCode",
    "productName",
    "brand",
    "abc",
    "status",
]
CHANGE_LOG_COLUMNS = [
    "date",
    "nmId",
    "change_type",
    "description",
]

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


def _copy_records(records):
    return [record.copy() for record in records]


def _to_int_if_possible(value):
    if isinstance(value, bool) or value is None:
        return value

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value) if value.is_integer() else value

    if isinstance(value, str):
        stripped_value = value.strip()

        if not stripped_value:
            return value

        try:
            numeric_value = float(stripped_value.replace(",", "."))
        except ValueError:
            return value

        return int(numeric_value) if numeric_value.is_integer() else value

    return value


def _is_active(row):
    return str(row.get("status") or "").strip().lower() == "active"


def _select_columns(row, columns):
    return {column: row.get(column, "") for column in columns}


def _normalize_numeric_fields(row):
    for field in ("seller_id", "nmId"):
        if field in row:
            row[field] = _to_int_if_possible(row[field])

    return row


def _google_sheets_configured():
    return bool(
        os.getenv(GOOGLE_SHEETS_CREDENTIALS_JSON_ENV)
        and os.getenv(GOOGLE_SHEETS_SPREADSHEET_ID_ENV)
    )


def get_google_client():
    credentials_json = os.environ[GOOGLE_SHEETS_CREDENTIALS_JSON_ENV]
    credentials_info = json.loads(credentials_json)
    credentials = Credentials.from_service_account_info(
        credentials_info,
        scopes=SCOPES,
    )

    return gspread.authorize(credentials)


@lru_cache(maxsize=None)
def _get_worksheet_records(worksheet_name):
    if not _google_sheets_configured():
        print("Google Sheets not configured, using stub data")
        return None

    try:
        client = get_google_client()
        spreadsheet = client.open_by_key(os.environ[GOOGLE_SHEETS_SPREADSHEET_ID_ENV])
        worksheet = spreadsheet.worksheet(worksheet_name)
        return worksheet.get_all_records()
    except Exception:
        print("Google Sheets read failed, using stub data")
        return None


@lru_cache(maxsize=None)
def _get_sellers_cached():
    records = _get_worksheet_records(SELLERS_WORKSHEET)

    if records is None:
        return _copy_records(STUB_SELLERS)

    sellers = []

    for row in records:
        seller = _normalize_numeric_fields(_select_columns(row, SELLER_COLUMNS))

        if _is_active(seller):
            sellers.append(seller)

    return sellers


@lru_cache(maxsize=None)
def _get_products_cached():
    records = _get_worksheet_records(PRODUCTS_WORKSHEET)

    if records is None:
        return _copy_records(STUB_PRODUCTS)

    products = []

    for row in records:
        product = _normalize_numeric_fields(_select_columns(row, PRODUCT_COLUMNS))

        if _is_active(product):
            products.append(product)

    return products


@lru_cache(maxsize=None)
def _get_change_log_cached():
    records = _get_worksheet_records(CHANGE_LOG_WORKSHEET)

    if records is None:
        return _copy_records(STUB_CHANGE_LOG)

    change_log = []

    for row in records:
        change = _normalize_numeric_fields(_select_columns(row, CHANGE_LOG_COLUMNS))
        change["changeType"] = change.get("change_type", "")
        change["comment"] = change.get("description", "")
        change_log.append(change)

    return change_log


def get_sellers():
    return _copy_records(_get_sellers_cached())


def get_products():
    return _copy_records(_get_products_cached())


def get_change_log():
    return _copy_records(_get_change_log_cached())


def create_tasks(tasks):
    print(f"TASKS TO CREATE: {len(tasks)}")

    for task in tasks[:5]:
        print(task)
