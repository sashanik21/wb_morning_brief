import time

import requests


class WBClient:
    def __init__(self, headers: dict):
        self.headers = headers

    def request(
        self,
        method: str,
        url: str,
        json_data: dict | None = None,
        retries: int = 5,
    ):
        for attempt in range(retries):
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                json=json_data,
                timeout=60,
            )

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError:
                    return response.text

            if response.status_code == 429:
                retry_time = int(response.headers.get("X-Ratelimit-Retry", 2))
                print(f"429 WB API LIMIT. WAIT {retry_time} seconds")
                time.sleep(retry_time)
                continue

            if response.status_code >= 500:
                wait_time = min(2**attempt, 30)
                print(
                    f"WB SERVER ERROR {response.status_code}. WAIT {wait_time} seconds"
                )
                time.sleep(wait_time)
                continue

            print("WB API ERROR")
            print("STATUS:", response.status_code)
            print("TEXT:", response.text)
            return None

        print("REQUEST FAILED AFTER RETRIES")
        return None
