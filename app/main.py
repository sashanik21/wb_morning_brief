import pandas as pd

from app.collectors.funnel import (
    collect_sales_funnel,
    save_funnel_problems_report,
    save_sales_funnel_report,
)
from app.reports.telegram_report import send_telegram_morning_brief
from app.sheets.google_sheets import get_sellers


def main():

    print("MAIN VERSION: TELEGRAM ENABLED")
    print("=" * 50)
    print("WB MORNING BRIEF")
    print("=" * 50)

    sellers = get_sellers()
    active_sellers = [seller for seller in sellers if seller.get("status") == "active"]
    print(f"Активных продавцов: {len(active_sellers)}")
    if active_sellers:
        print(f"Текущий продавец: {active_sellers[0]['seller_name']}")

    data = collect_sales_funnel()

    if data is None:
        print("Данные funnel не получены")
        return

    print("FUNNEL ДАННЫЕ ПОЛУЧЕНЫ")
    print("=" * 50)

    report_path = save_sales_funnel_report(data)
    print(f"XLSX отчёт: {report_path}")
    print("=" * 50)

    problems_report_path = save_funnel_problems_report(data)
    print(f"XLSX отчёт по проблемам: {problems_report_path}")
    print("=" * 50)

    problems = (
        pd.read_excel(problems_report_path, sheet_name="problems")
        .fillna("")
        .to_dict("records")
    )
    print("ОТПРАВЛЯЕМ TELEGRAM MORNING BRIEF")
    send_telegram_morning_brief(problems)
    print("=" * 50)

    print("WB Morning Brief completed successfully")


if __name__ == "__main__":
    main()
