import os


WB_API_TOKEN = os.getenv("WB_API_TOKEN_TEST")

if not WB_API_TOKEN:
    raise ValueError("WB_API_TOKEN_TEST не найден в GitHub Secrets")


HEADERS = {
    "Authorization": WB_API_TOKEN
}
