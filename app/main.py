import json
from datetime import datetime

from app.collectors.funnel import collect_sales_funnel


def main():
    print("WB Morning Brief MVP started")
    print("Started at:", datetime.now().isoformat())

    data = collect_sales_funnel()

    if data is None:
        print("Данные не получены")
        return

    print("Данные успешно получены")
    print(json.dumps(data, ensure_ascii=False, indent=2)[:5000])


if __name__ == "__main__":
    main()
