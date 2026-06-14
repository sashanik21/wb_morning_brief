import pandas as pd

from app.analyzers.ads_analyzer import (
    analyze_ads_problems,
    build_ads_summary,
    enrich_ads_time_series,
    save_ads_report,
)
from app.analyzers.forecast_engine import build_predictive_forecasts
from app.analyzers.products_enrichment import enrich_funnel_data_with_products
from app.analyzers.root_cause_analyzer import analyze_root_causes
from app.analyzers.tasks_builder import build_tasks_from_problems
from app.collectors.ads import collect_ads_stats
from app.collectors.funnel import (
    build_top_funnel_drop_signals,
    calculate_funnel_summary_dynamics,
    collect_sales_funnel,
    count_sku_ignored_by_abc_filter,
    flatten_sales_funnel_data,
    save_funnel_problems_report,
    save_sales_funnel_report,
)
from app.collectors.supplies import collect_supply_stock_metrics
from app.reports.evidence import EVIDENCE_LIMIT_TELEGRAM, build_evidence_rows
from app.reports.telegram_report import send_telegram_morning_brief
from app.storage.storage_factory import get_storage


def _extract_funnel_products(data):
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


def _seller_id(seller):
    if not isinstance(seller, dict):
        return None

    return seller.get("seller_id") or seller.get("id")


def _to_float(value):
    if value in (None, ""):
        return 0
    try:
        return float(str(value).replace("%", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return 0


def _sum_report_column(dataframe, column_name):
    if column_name not in dataframe:
        return 0

    numeric_values = pd.to_numeric(dataframe[column_name], errors="coerce").fillna(0)
    total = numeric_values.sum()

    return int(total) if float(total).is_integer() else round(float(total), 2)


def _summary_total(summary_dynamics, summary_key, fallback_report, fallback_column):
    summary_value = summary_dynamics.get(summary_key)

    if summary_value not in (None, ""):
        return summary_value

    return _sum_report_column(fallback_report, fallback_column)


def _build_summary_stats(
    storage_status,
    seller_name,
    total_sku_from_api,
    sku_in_products,
    sku_not_in_products,
    below_abc_threshold_problems,
    critical_problems_count,
    funnel_data,
    supply_stock_metrics_by_nm_id=None,
):
    funnel_report = flatten_sales_funnel_data(funnel_data)
    funnel_summary_dynamics = calculate_funnel_summary_dynamics(funnel_data)

    supply_stock_metrics = supply_stock_metrics_by_nm_id or {}
    supply_totals = {
        "incomingStock": sum(
            _to_float(metric.get("incomingStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "acceptanceStock": sum(
            _to_float(metric.get("acceptanceStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "transitStock": sum(
            _to_float(metric.get("transitStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "readyForSaleStock": sum(
            _to_float(metric.get("readyForSaleStock"))
            for metric in supply_stock_metrics.values()
            if isinstance(metric, dict)
        ),
        "matchedSkuCount": len(supply_stock_metrics),
    }

    return {
        "sellerName": seller_name,
        "storage": storage_status,
        "totalSkuFromApi": total_sku_from_api,
        "skuInProducts": sku_in_products,
        "skuNotInProducts": sku_not_in_products,
        "skuAfterProductsFilter": total_sku_from_api,
        "skuRemovedByProductsFilter": 0,
        "belowAbcThresholdProblems": below_abc_threshold_problems,
        "criticalProblemsCount": critical_problems_count,
        "totalOrders": _summary_total(
            funnel_summary_dynamics, "selectedOrderCount", funnel_report, "orderCount"
        ),
        "totalOrderSum": _summary_total(
            funnel_summary_dynamics, "selectedOrderSum", funnel_report, "orderSum"
        ),
        "totalOpenCount": _summary_total(
            funnel_summary_dynamics, "selectedOpenCount", funnel_report, "openCount"
        ),
        "totalCartCount": _summary_total(
            funnel_summary_dynamics, "selectedCartCount", funnel_report, "cartCount"
        ),
        "topDropSignals": build_top_funnel_drop_signals(funnel_data),
        "evidenceRows": build_evidence_rows(funnel_data, limit=EVIDENCE_LIMIT_TELEGRAM),
        "funnelData": funnel_data,
        "supplyStockMetrics": supply_totals,
        **funnel_summary_dynamics,
    }


def _print_summary_stats(summary_stats):
    print("MORNING BRIEF SUMMARY:")
    print(f"totalSkuFromApi: {summary_stats['totalSkuFromApi']}")
    print(f"skuInProducts: {summary_stats['skuInProducts']}")
    print(f"skuNotInProducts: {summary_stats['skuNotInProducts']}")
    print(f"belowAbcThresholdProblems: {summary_stats['belowAbcThresholdProblems']}")


def main():

    print("MAIN VERSION: TELEGRAM ENABLED")
    print("=" * 50)
    print("WB MORNING BRIEF")
    print("=" * 50)
    storage = get_storage()
    print("=" * 50)

    sellers = storage.get_sellers()
    print(f"SELLERS LOADED: {len(sellers)}")
    active_sellers = [seller for seller in sellers if seller.get("status") == "active"]
    print(f"Активных продавцов: {len(active_sellers)}")
    current_seller = active_sellers[0] if active_sellers else {}
    seller_name = current_seller.get("seller_name", "")
    seller_id = _seller_id(current_seller)
    if active_sellers:
        print(f"Текущий продавец: {seller_name}")

    products = storage.get_products()
    print(f"PRODUCTS LOADED: {len(products)}")

    change_log = storage.get_change_log()
    print(f"CHANGE_LOG LOADED: {len(change_log)}")

    data = collect_sales_funnel()

    if data is None:
        print("Данные funnel не получены")
        return

    print("FUNNEL ДАННЫЕ ПОЛУЧЕНЫ")
    print("=" * 50)

    wb_cards = _extract_funnel_products(data)
    storage.sync_products_from_wb_cards(seller_id, wb_cards)
    products = storage.get_products()
    print(f"PRODUCTS LOADED: {len(products)}")

    total_sku_from_api = len(wb_cards)
    data = enrich_funnel_data_with_products(data, products)
    enriched_products = _extract_funnel_products(data)
    sku_in_products = sum(
        1
        for funnel_product in enriched_products
        if (
            funnel_product.get("product", funnel_product)
            if isinstance(funnel_product, dict)
            else {}
        ).get("productInCatalog")
        is True
    )
    sku_not_in_products = len(enriched_products) - sku_in_products
    print("=" * 50)

    ads_data = collect_ads_stats()
    ads_data = enrich_ads_time_series(ads_data, storage=storage, seller_id=seller_id)
    funnel_report = flatten_sales_funnel_data(data)
    funnel_rows = funnel_report.to_dict("records")
    for funnel_row in funnel_rows:
        funnel_row["seller_id"] = seller_id
    ads_problems = analyze_ads_problems(ads_data, funnel_report)
    ads_summary = build_ads_summary(ads_data, ads_problems)
    print(f"ADS ДАННЫЕ ПОЛУЧЕНЫ: {len(ads_data)} строк")
    print("ADS SUMMARY:")
    print(f"campaigns: {ads_summary['activeCampaigns']}")
    print(f"ads rows: {ads_summary['adsRows']}")
    print(f"problems: {ads_summary['problems']}")
    print(
        "period: "
        f"{ads_summary.get('selectedPeriod') or 'n/a'} vs "
        f"{ads_summary.get('pastPeriod') or 'n/a'}"
    )
    print("=" * 50)

    stocks_problems = []
    try:
        supply_stock_metrics_by_nm_id = collect_supply_stock_metrics()
    except Exception as error:
        print(f"SUPPLIES COLLECTOR WARNING: {error}")
        print("SUPPLIES DATA: 0 rows")
        print("SUPPLIES API:")
        print("status: disabled_or_failed")
        print(f"reason: {error}")
        supply_stock_metrics_by_nm_id = {}

    report_path = save_sales_funnel_report(data)
    print(f"XLSX отчёт: {report_path}")
    print("=" * 50)

    predictive_forecasts = build_predictive_forecasts(
        funnel_rows, ads_rows=ads_data, storage=storage, seller_id=seller_id
    )
    print(f"PREDICTIVE FORECASTS: {len(predictive_forecasts)}")

    ads_report_path = save_ads_report(ads_data, ads_problems)
    print(f"XLSX отчёт по рекламе: {ads_report_path}")
    print("=" * 50)

    # TODO: switch problems XLSX generation to all_problems after ads/stocks problems are enabled
    problems_report_path = save_funnel_problems_report(
        data,
        seller_id=seller_id,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
        ads_rows=ads_data,
        predictive_forecasts=predictive_forecasts,
    )
    print(f"XLSX отчёт по проблемам: {problems_report_path}")
    print("=" * 50)

    funnel_problems_df = pd.read_excel(
        problems_report_path, sheet_name="problems"
    ).fillna("")
    funnel_problems = funnel_problems_df.to_dict("records")
    all_problems = funnel_problems + ads_problems + stocks_problems
    if funnel_rows:
        storage.save_funnel_snapshot(funnel_rows)
    if all_problems:
        storage.save_problems(all_problems)
    root_cause_insights = analyze_root_causes(all_problems, data)
    print(f"ROOT CAUSE INSIGHTS: {len(root_cause_insights)}")
    summary_stats = _build_summary_stats(
        storage_status=storage.get_storage_status(),
        seller_name=seller_name,
        total_sku_from_api=total_sku_from_api,
        sku_in_products=sku_in_products,
        sku_not_in_products=sku_not_in_products,
        below_abc_threshold_problems=count_sku_ignored_by_abc_filter(data),
        critical_problems_count=len(all_problems),
        funnel_data=data,
        supply_stock_metrics_by_nm_id=supply_stock_metrics_by_nm_id,
    )

    summary_stats["adsSummary"] = ads_summary
    _print_summary_stats(summary_stats)
    print("=" * 50)

    print("TOTAL PROBLEMS:")
    print(f"funnel: {len(funnel_problems)}")
    print(f"ads: {len(ads_problems)}")
    print(f"stocks: {len(stocks_problems)}")
    print(f"all: {len(all_problems)}")
    print("=" * 50)

    print("ОТПРАВЛЯЕМ TELEGRAM MORNING BRIEF")
    send_telegram_morning_brief(
        all_problems,
        summary_stats=summary_stats,
        root_cause_insights=root_cause_insights,
    )
    print("=" * 50)

    tasks = build_tasks_from_problems(all_problems)
    storage.create_tasks(tasks)
    print("=" * 50)

    print("WB Morning Brief completed successfully")


if __name__ == "__main__":
    main()
