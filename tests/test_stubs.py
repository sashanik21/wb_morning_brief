import pandas as pd

from app.analyzers.ads_analyzer import _funnel_rows_by_nm_id, analyze_ads_problems
from app.analyzers.products_enrichment import enrich_funnel_data_with_products
from app.analyzers.root_cause_analyzer import analyze_root_causes


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


def _problem(nm_id, metric, dynamic_percent=-20, selected_value=10):
    return {
        "nmId": nm_id,
        "vendorCode": f"vendor-{nm_id}",
        "title": f"Товар {nm_id}",
        "metric": metric,
        "problemLabel": metric,
        "selectedValue": selected_value,
        "dynamicPercent": dynamic_percent,
    }


def test_root_cause_order_drop_with_open_drop_is_traffic():
    insights = analyze_root_causes(
        [_problem(1, "orderCount"), _problem(1, "openCount")], []
    )

    assert insights[0]["rootCauseZone"] == "Верх воронки / трафик"


def test_root_cause_order_drop_with_stable_opens_and_cart_drop_is_card():
    insights = analyze_root_causes(
        [_problem(2, "orderCount"), _problem(2, "cartCount")], []
    )

    assert insights[0]["rootCauseZone"] == "Карточка / цена / УТП"


def test_root_cause_order_drop_with_stable_carts_and_cart_to_order_drop_is_delivery():
    insights = analyze_root_causes(
        [_problem(3, "orderCount"), _problem(3, "cartToOrderPercent")], []
    )

    assert insights[0]["rootCauseZone"] == "Доставка / остатки / цена на этапе заказа"


def test_root_cause_wb_stock_zero_is_stock_zone():
    insights = analyze_root_causes(
        [_problem(4, "orderCount"), _problem(4, "wbStocks", selected_value=0)], []
    )

    assert insights[0]["rootCauseZone"] == "Остатки WB"


def _ads_row(**overrides):
    row = {
        "campaignId": 101,
        "campaignName": "Test campaign",
        "nmId": 1001,
        "vendorCode": "vendor-1001",
        "title": "Тестовый товар",
        "impressions": 1000,
        "clicks": 20,
        "ctr": 2.0,
        "cpc": 50,
        "cpm": 1000,
        "orders": 1,
        "ordersSum": 1000,
        "spend": 100,
        "drr": 10,
        "previousImpressions": 1000,
        "previousClicks": 30,
        "previousCtr": 4.0,
        "previousCpc": 40,
        "previousCpm": 900,
        "previousOrders": 1,
        "previousOrdersSum": 1000,
        "previousSpend": 100,
        "previousDrr": 10,
        "date": "2026-06-12",
    }
    row.update(overrides)
    return row


def _problem_types(problems):
    return {problem["problemType"] for problem in problems}


def test_funnel_rows_by_nm_id_handles_none():
    assert _funnel_rows_by_nm_id(None) == {}


def test_funnel_rows_by_nm_id_handles_empty_list():
    assert _funnel_rows_by_nm_id([]) == {}


def test_funnel_rows_by_nm_id_handles_empty_dataframe():
    assert _funnel_rows_by_nm_id(pd.DataFrame()) == {}


def test_funnel_rows_by_nm_id_maps_dataframe_by_nm_id():
    dataframe = pd.DataFrame(
        [
            {"nmId": 1001, "openCount": 20},
            {"nmId": 1002, "openCount": 30},
        ]
    )

    assert _funnel_rows_by_nm_id(dataframe) == {
        "1001": {"nmId": 1001, "openCount": 20},
        "1002": {"nmId": 1002, "openCount": 30},
    }


def test_ads_analyzer_detects_low_ctr():
    problems = analyze_ads_problems([_ads_row(ctr=2.1, previousCtr=2.1)])

    assert "ads_ctr_low" in _problem_types(problems)


def test_ads_analyzer_detects_high_drr():
    problems = analyze_ads_problems([_ads_row(drr=39, ordersSum=1000, spend=390)])

    assert "ads_drr_growth" in _problem_types(problems)


def test_ads_analyzer_detects_spend_without_orders():
    problems = analyze_ads_problems([_ads_row(spend=250, orders=0)])

    assert "ads_spend_without_orders" in _problem_types(problems)


def test_ads_analyzer_detects_cpc_growth():
    problems = analyze_ads_problems([_ads_row(cpc=47, previousCpc=35)])

    assert "ads_cpc_growth" in _problem_types(problems)
