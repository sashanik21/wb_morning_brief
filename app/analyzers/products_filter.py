from copy import deepcopy

ACTIVE_STATUS = "active"


def _is_missing(value):
    if value is None:
        return True

    if isinstance(value, str) and value.strip() == "":
        return True

    return False


def _normalize_nm_id(value):
    if _is_missing(value):
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value) if value.is_integer() else None

    if isinstance(value, str):
        stripped_value = value.strip()

        if not stripped_value:
            return None

        try:
            numeric_value = float(stripped_value.replace(",", "."))
        except ValueError:
            return None

        return int(numeric_value) if numeric_value.is_integer() else None

    return None


def _is_active_product(product):
    status = str(product.get("status", ACTIVE_STATUS)).strip().lower()
    return status == ACTIVE_STATUS


def _active_nm_ids(products):
    active_nm_ids = set()

    for product in products:
        if not isinstance(product, dict) or not _is_active_product(product):
            continue

        nm_id = _normalize_nm_id(product.get("nmId"))

        if nm_id is not None:
            active_nm_ids.add(nm_id)

    return active_nm_ids


def _product_nm_id(funnel_product):
    if not isinstance(funnel_product, dict):
        return None

    product = funnel_product.get("product")

    if isinstance(product, dict):
        nm_id = _normalize_nm_id(product.get("nmId"))

        if nm_id is not None:
            return nm_id

    return _normalize_nm_id(funnel_product.get("nmId"))


def _filter_products(funnel_products, active_nm_ids):
    return [
        funnel_product
        for funnel_product in funnel_products
        if _product_nm_id(funnel_product) in active_nm_ids
    ]


def _filter_data_products(data, active_nm_ids):
    filtered_data = deepcopy(data)

    if isinstance(filtered_data, list):
        return _filter_products(filtered_data, active_nm_ids)

    if not isinstance(filtered_data, dict):
        return filtered_data

    nested_data = filtered_data.get("data")

    if isinstance(nested_data, dict) and isinstance(nested_data.get("products"), list):
        nested_data["products"] = _filter_products(
            nested_data["products"], active_nm_ids
        )
        return filtered_data

    if isinstance(filtered_data.get("products"), list):
        filtered_data["products"] = _filter_products(
            filtered_data["products"], active_nm_ids
        )
        return filtered_data

    if isinstance(nested_data, list):
        filtered_data["data"] = _filter_products(nested_data, active_nm_ids)

    return filtered_data


def _extract_products(data):
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    nested_data = data.get("data")

    if isinstance(nested_data, dict) and isinstance(nested_data.get("products"), list):
        return nested_data["products"]

    if isinstance(data.get("products"), list):
        return data["products"]

    if isinstance(nested_data, list):
        return nested_data

    return []


def filter_funnel_data_by_products(data, products):
    if not products:
        print("PRODUCTS EMPTY — FUNNEL FILTER SKIPPED")
        return data

    active_nm_ids = _active_nm_ids(products)
    before = len(_extract_products(data))
    filtered_data = _filter_data_products(data, active_nm_ids)
    after = len(_extract_products(filtered_data))
    removed = before - after

    print("FUNNEL SKU FILTER:")
    print(f"before: {before}")
    print(f"after: {after}")
    print(f"removed: {removed}")

    if after == 0:
        print("FUNNEL SKU FILTER RESULT IS EMPTY")

    return filtered_data
