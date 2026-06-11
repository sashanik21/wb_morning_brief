from app.analyzers.products_enrichment import enrich_funnel_data_with_products


def test_enrichment_keeps_all_wb_api_skus():
    data = {
        "data": {
            "products": [
                {"product": {"nmId": 1}, "history": []},
                {"product": {"nmId": 2}, "history": []},
            ]
        }
    }
    products = [{"nmId": 2, "abc": "A", "status": "active"}]

    result = enrich_funnel_data_with_products(data, products)

    assert [item["product"]["nmId"] for item in result["data"]["products"]] == [1, 2]


def test_matched_sku_gets_products_abc_and_catalog_flag():
    data = {"data": {"products": [{"product": {"nmId": 2}, "history": []}]}}
    products = [{"nmId": 2, "abc": "A", "status": "active"}]

    result = enrich_funnel_data_with_products(data, products)
    enriched_product = result["data"]["products"][0]["product"]

    assert enriched_product["ABC"] == "A"
    assert enriched_product["productStatus"] == "active"
    assert enriched_product["productInCatalog"] is True


def test_missing_sku_gets_unknown_abc_and_missing_catalog_flag():
    data = {"data": {"products": [{"product": {"nmId": 1}, "history": []}]}}
    products = [{"nmId": 2, "abc": "A", "status": "active"}]

    result = enrich_funnel_data_with_products(data, products)
    enriched_product = result["data"]["products"][0]["product"]

    assert enriched_product["ABC"] == "UNKNOWN"
    assert enriched_product["productStatus"] == "not_in_products"
    assert enriched_product["productInCatalog"] is False


def test_data_without_products_does_not_crash():
    data = {"data": {"total": 0}}
    products = [{"nmId": 1, "abc": "A", "status": "active"}]

    result = enrich_funnel_data_with_products(data, products)

    assert result == data
