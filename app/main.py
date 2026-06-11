import json

from app.collectors.funnel import collect_sales_funnel, save_sales_funnel_report


def main():

    print("=" * 50)
    print("WB MORNING BRIEF")
    print("=" * 50)

    data = collect_sales_funnel()

    if data is None:
        print("Данные funnel не получены")
        return

    print("FUNNEL ДАННЫЕ ПОЛУЧЕНЫ")
    print("=" * 50)

    report_path = save_sales_funnel_report(data)
    print(f"XLSX отчёт: {report_path}")
    print("=" * 50)

    print(json.dumps(data, ensure_ascii=False, indent=2)[:15000])


if __name__ == "__main__":
    main()
