from copy import deepcopy

from app.analyzers.perfume_intelligence import parse_perfume_title

UNKNOWN_ABC = "UNKNOWN"
MISSING_PRODUCT_STATUS = "not_in_products"


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


def _products_by_nm_id(products):
    indexed_products = {}

    for product in products or []:
        if not isinstance(product, dict):
            continue

        nm_id = _normalize_nm_id(product.get("nmId"))

        if nm_id is not None:
            indexed_products[nm_id] = product

    return indexed_products


def _funnel_product_nm_id(funnel_product):
    if not isinstance(funnel_product, dict):
        return None

    product = funnel_product.get("product")

    if isinstance(product, dict):
        nm_id = _normalize_nm_id(product.get("nmId"))

        if nm_id is not None:
            return nm_id

    return _normalize_nm_id(funnel_product.get("nmId"))


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


def _product_field_container(funnel_product):
    if not isinstance(funnel_product, dict):
        return None

    product = funnel_product.get("product")

    if isinstance(product, dict):
        return product

    return funnel_product


def _first_present(mapping, keys, default=""):
    for key in keys:
        value = mapping.get(key)

        if not _is_missing(value):
            return value

    return default


def _enrich_product(funnel_product, products_by_nm_id):
    product_fields = _product_field_container(funnel_product)

    if product_fields is None:
        return False

    product = products_by_nm_id.get(_funnel_product_nm_id(funnel_product))

    if product:
        product_fields["ABC"] = str(product.get("abc") or UNKNOWN_ABC).upper()
        product_fields["productStatus"] = _first_present(
            product, ["productStatus", "status"], default=""
        )
        product_fields["productInCatalog"] = True
        product_fields["title"] = _first_present(
            product,
            ["productName", "title", "name"],
            default=product_fields.get("title", ""),
        )
        product_fields["brandName"] = _first_present(
            product, ["brand", "brandName"], default=product_fields.get("brandName", "")
        )
        product_fields["vendorCode"] = _first_present(
            product,
            ["vendorCode", "supplierArticle", "article"],
            default=product_fields.get("vendorCode", ""),
        )
        product_fields.update(
            parse_perfume_title(
                product_fields.get("title"), product_fields.get("brandName")
            )
        )
        return True

    product_fields["ABC"] = UNKNOWN_ABC
    product_fields["productStatus"] = MISSING_PRODUCT_STATUS
    product_fields["productInCatalog"] = False
    product_fields.update(
        parse_perfume_title(
            product_fields.get("title"), product_fields.get("brandName")
        )
    )
    return False


def enrich_funnel_data_with_products(data, products):
    enriched_data = deepcopy(data)
    products_by_nm_id = _products_by_nm_id(products)
    funnel_products = _extract_products(enriched_data)
    matched_count = 0

    for funnel_product in funnel_products:
        if _enrich_product(funnel_product, products_by_nm_id):
            matched_count += 1

    total_count = len(funnel_products)
    missing_count = total_count - matched_count

    print("PRODUCTS ENRICHMENT:")
    print(f"sku from WB API: {total_count}")
    print(f"matched in PRODUCTS: {matched_count}")
    print(f"not found in PRODUCTS: {missing_count}")

    return enriched_data
