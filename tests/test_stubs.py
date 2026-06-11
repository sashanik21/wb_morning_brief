from app.analyzers.products_filter import filter_funnel_data_by_products


def test_empty_products_keeps_funnel_data_unchanged():
    data = {"data": {"products": [{"product": {"nmId": 1}}]}}

    result = filter_funnel_data_by_products(data, [])

    assert result is data
    assert result == data


def test_products_with_nm_id_keep_only_matching_skus():
    data = {
        "data": {
            "products": [
                {"product": {"nmId": 1}, "history": []},
                {"product": {"nmId": 2}, "history": []},
            ]
        }
    }
    products = [{"nmId": 2, "status": "active"}]

    result = filter_funnel_data_by_products(data, products)

    assert result["data"]["products"] == [{"product": {"nmId": 2}, "history": []}]


def test_invalid_product_nm_id_is_ignored():
    data = {
        "data": {
            "products": [
                {"product": {"nmId": 1}, "history": []},
                {"product": {"nmId": 2}, "history": []},
            ]
        }
    }
    products = [
        {"nmId": "not-an-id", "status": "active"},
        {"nmId": 1, "status": "active"},
    ]

    result = filter_funnel_data_by_products(data, products)

    assert result["data"]["products"] == [{"product": {"nmId": 1}, "history": []}]


def test_data_without_products_does_not_crash():
    data = {"data": {"total": 0}}
    products = [{"nmId": 1, "status": "active"}]

    result = filter_funnel_data_by_products(data, products)

    assert result == data
