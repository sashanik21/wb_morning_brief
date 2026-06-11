import pandas as pd

from app.analyzers.ads_analyzer import analyze_ads_problems
from app.analyzers.products_filter import filter_funnel_data_by_products
from app.collectors.ads import collect_ads_stats
from app.collectors.funnel import (
    collect_sales_funnel,
    save_funnel_problems_report,
    save_sales_funnel_report,
)
from app.reports.telegram_report import send_telegram_morning_brief
from app.sheets.google_sheets import get_change_log, get_products, get_sellers


def main():

    print("MAIN VERSION: TELEGRAM ENABLED")
    print("=" * 50)
    print("WB MORNING BRIEF")
    print("=" * 50)

    sellers = get_sellers()
    print(f"SELLERS LOADED: {len(sellers)}")
    active_sellers = [seller for seller in sellers if seller.get("status") == "active"]
    print(f"Активных продавцов: {len(active_sellers)}")
    if active_sellers:
        print(f"Текущий продавец: {active_sellers[0]['seller_name']}")

    products = get_products()
    print(f"PRODUCTS LOADED: {len(products)}")

    change_log = get_change_log()
    print(f"CHANGE_LOG LOADED: {len(change_log)}")

    data = collect_sales_funnel()

    if data is None:
        print("Данные funnel не получены")
        return

    print("FUNNEL ДАННЫЕ ПОЛУЧЕНЫ")
    print("=" * 50)

    data = filter_funnel_data_by_products(data, products)
    print("=" * 50)

    ads_data = collect_ads_stats()
    ads_problems = analyze_ads_problems(ads_data)
    print(f"ADS ДАННЫЕ ПОЛУЧЕНЫ: {len(ads_data)} строк")
    print("=" * 50)

    report_path = save_sales_funnel_report(data)
    print(f"XLSX отчёт: {report_path}")
    print("=" * 50)

    problems_report_path = save_funnel_problems_report(data)
    print(f"XLSX отчёт по проблемам: {problems_report_path}")
    print("=" * 50)

    funnel_problems = (
        pd.read_excel(problems_report_path, sheet_name="problems")
        .fillna("")
        .to_dict("records")
    )
    all_problems = funnel_problems + ads_problems

    print("ОТПРАВЛЯЕМ TELEGRAM MORNING BRIEF")
    send_telegram_morning_brief(funnel_problems)
    print("=" * 50)

    print("WB Morning Brief completed successfully")


if __name__ == "__main__":
    main()
