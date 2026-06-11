# Repo audit: `wb_morning_brief`

Дата аудита: 2026-06-11.

Цель: зафиксировать фактическое состояние репозитория `sashanik21/wb_morning_brief` после ошибки, когда часть задач могла быть выполнена в другом репозитории (`sashanik21/fm26-bot333`). Python-код, workflow и `requirements.txt` не изменялись; создан только этот отчёт.

## 1. Текущий репозиторий и ветка

| Поле | Значение |
|---|---|
| repo | `sashanik21/wb_morning_brief` по пользовательскому указанию; в локальном git remote `origin` не настроен |
| local path | `/workspace/wb_morning_brief` |
| branch | `work` |
| latest commit hash | `788a8ad19470b7222736120aa8337dc4ccebebb2` |
| latest commit message | `Merge pull request #18 from sashanik21/codex/complete-products-whitelist-for-funnel-analysis` |

Проверялось командами:

```bash
git status --short --branch
git config --get remote.origin.url || true
git log -1 --pretty=format:'%H%n%s'
```

## 2. Список файлов и папок верхнего уровня

Фактически обнаружены:

```text
.git/
.github/
.isort.cfg
README.md
app/
requirements.txt
tests/
```

Примечание: папки `docs/` до создания этого отчёта не было.

## 3. Наличие и состояние ключевых файлов

| Файл | Статус | Состояние |
|---|---:|---|
| `.github/workflows/morning_brief.yml` | DONE | Workflow существует, запускается по `workflow_dispatch` и cron `30 4 * * *`, устанавливает Python 3.12, ставит зависимости из `requirements.txt`, запускает `python -m app.main`, загружает `reports/` как artifact. Не передаёт Google Sheets secrets. |
| `requirements.txt` | PARTIAL | Есть `requests`, `python-dotenv`, `pandas`, `openpyxl`. Нет `pytest`, `black`, `isort`, `gspread`, `google-auth`. |
| `app/main.py` | PARTIAL | Есть основной pipeline: sellers/products из `app.sheets.google_sheets`, funnel collection, products filter, ads stub, XLSX reports, Telegram. Переменная `all_problems` создаётся, но в Telegram отправляются только `funnel_problems`, ads-проблемы не используются. |
| `app/config.py` | DONE | Читает `WB_API_TOKEN_TEST`, формирует `HEADERS`, содержит `ABC_RULES`. Импортирует `SELLER_CONFIG`, `SELLER_NAME`, но не использует их в файле. |
| `app/wb_client.py` | DONE | Реализован простой WB API client через `requests.request` с timeout, retry для `429` и `5xx`, JSON/text parsing. |
| `app/collectors/funnel.py` | DONE | Реализован сбор sales funnel через `seller-analytics-api.wildberries.ru/api/analytics/v3/sales-funnel/products`, payload с `selectedPeriod`, `pastPeriod`, `nmIds`, сохранение funnel XLSX и problems XLSX, ABC filter, recentChanges. |
| `app/collectors/cards.py` | DONE | Реализован запрос списка карточек WB Content API `/content/v2/get/cards/list`, limit 10. |
| `app/collectors/ads.py` | STUB | Файл есть, но `collect_ads_stats()` только печатает stub-сообщения и возвращает пустой список. |
| `app/collectors/stocks.py` | MISSING | Файл отсутствует. |
| `app/analyzers/funnel_analyzer.py` | MISSING | Файл отсутствует; фактический funnel-анализ проблем находится внутри `app/collectors/funnel.py`. |
| `app/analyzers/ads_analyzer.py` | STUB | Файл есть, но `analyze_ads_problems()` всегда возвращает пустой список. |
| `app/analyzers/stocks_analyzer.py` | MISSING | Файл отсутствует. |
| `app/analyzers/products_filter.py` | DONE | Реализует whitelist/filter по active `nmId`, поддерживает разные формы структуры funnel data, покрыт smoke-тестами. |
| `app/reports/telegram_report.py` | DONE | Реализует HTML Telegram Morning Brief, группировку проблем по товару, seller name в заголовке, recentChanges и рекомендации, отправку через Telegram Bot API. |
| `app/reports/pdf_report.py` | MISSING | Файл отсутствует. |
| `app/sheets/google_sheets.py` | STUB | Файл есть, но Google Sheets API не используется: `get_sellers()`, `get_products()`, `get_change_log()` возвращают hardcoded данные; `create_tasks()` только печатает количество задач. |
| `app/sheets/__init__.py` | DONE | Файл существует, пустой package marker. |
| `tests/test_stubs.py` | DONE | Есть 4 smoke-теста для `filter_funnel_data_by_products()`: пустой products, фильтрация matching SKU, игнор invalid `nmId`, отсутствие products в data. |
| `docs/CURRENT_STATE.md` | MISSING | Файл отсутствует. |

## 4. Что реально реализовано

| Пункт | Статус | Фактическое состояние |
|---|---:|---|
| GitHub Actions workflow | DONE | Workflow есть и запускает приложение. |
| WB funnel API | DONE | Есть POST на sales funnel endpoint. |
| получение nmIds | DONE | `collect_sales_funnel()` получает карточки через `get_cards_list()` и собирает `nmID` из `cards`. |
| selectedPeriod / pastPeriod | DONE | Payload строит вчерашний день как `selectedPeriod` и день до него как `pastPeriod`. |
| funnel XLSX report | DONE | `save_sales_funnel_report()` сохраняет `reports/funnel_YYYY_MM_DD.xlsx`. |
| problems XLSX report | DONE | `save_funnel_problems_report()` сохраняет `reports/problems_YYYY_MM_DD.xlsx`. |
| Telegram Morning Brief | DONE | Есть формирование и отправка Telegram-сообщения. |
| Telegram secrets env | DONE | Workflow передаёт `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID`; код читает эти env vars. |
| sellerName в reports | DONE | В funnel/problems report columns есть `sellerName`, строки заполняются из `SELLER_NAME`. |
| sellerName в Telegram | DONE | Header Telegram включает `Продавец: <SELLER_NAME>`. |
| Google Sheets SELLERS reading | STUB | `get_sellers()` возвращает hardcoded список, без `gspread`/Google API/secrets. |
| Google Sheets PRODUCTS reading | STUB | `get_products()` возвращает hardcoded список, без `gspread`/Google API/secrets. |
| PRODUCTS whitelist | DONE | `filter_funnel_data_by_products()` фильтрует funnel data по active `nmId` из products. Источник products пока stub. |
| ABC filter | DONE | `ABC_RULES` есть, `_passes_abc_filter()` применяет thresholds перед добавлением проблем. |
| CHANGE_LOG / recentChanges | PARTIAL | `get_change_log()` hardcoded; `_build_recent_changes_by_nm_id()` добавляет recent changes за последние 7 дней в problems report/Telegram. Нет чтения из Google Sheets. |
| TASKS generation | STUB | `create_tasks(tasks)` существует только как print stub и не интегрирован в pipeline. |
| Ads collector | STUB | Возвращает `[]`, реального API/данных нет. |
| Ads analyzer | STUB | Возвращает `[]`, реального анализа нет. |
| Stocks collector | MISSING | `app/collectors/stocks.py` отсутствует. |
| Stocks analyzer | MISSING | `app/analyzers/stocks_analyzer.py` отсутствует. |
| all_problems pipeline | BROKEN | `all_problems = funnel_problems + ads_problems` создаётся, но дальше не используется; в Telegram передаётся `funnel_problems`. Stocks pipeline отсутствует. |
| pytest dependency | MISSING | `pytest` отсутствует в `requirements.txt`, хотя в текущей среде установлен и smoke-тесты проходят. |
| black/isort formatting | PARTIAL | `.isort.cfg` есть, `black --check` и `isort --check-only` проходят в текущей среде, но `black` и `isort` отсутствуют в `requirements.txt`. |

## 5. Workflow audit

Файл: `.github/workflows/morning_brief.yml`.

| Проверка | Статус | Факт |
|---|---:|---|
| Какой entrypoint запускается | DONE | Запускается `python -m app.main`. |
| Передаётся ли `WB_API_TOKEN_TEST` | DONE | Да: `WB_API_TOKEN_TEST: ${{ secrets.WB_API_TOKEN_TEST }}`. |
| Передаются ли `TELEGRAM_BOT_TOKEN` и `TELEGRAM_CHAT_ID` | DONE | Да, оба env vars передаются в шаг `Run WB Morning Brief`. |
| Передаются ли `GOOGLE_SHEETS_CREDENTIALS_JSON` и `GOOGLE_SHEETS_SPREADSHEET_ID` | MISSING | Нет, эти secrets/env vars в workflow отсутствуют. |
| Загружаются ли artifacts `reports/` | DONE | Да, `actions/upload-artifact@v4`, `name: funnel-reports`, `path: reports/`, `if-no-files-found: warn`. |

## 6. `requirements.txt` audit

| Dependency | Статус | Факт |
|---|---:|---|
| `requests` | DONE | Есть. |
| `pandas` | DONE | Есть. |
| `openpyxl` | DONE | Есть. |
| `pytest` | MISSING | Отсутствует. |
| `black` | MISSING | Отсутствует. |
| `isort` | MISSING | Отсутствует. |
| `gspread` | MISSING | Отсутствует. |
| `google-auth` | MISSING | Отсутствует. |

Текущее содержимое:

```text
requests
python-dotenv
pandas
openpyxl
```

## 7. Tests audit

| Проверка | Статус | Факт |
|---|---:|---|
| Есть ли `tests/test_stubs.py` | DONE | Да. |
| Что тесты покрывают | PARTIAL | Покрывают только `filter_funnel_data_by_products()` и edge cases вокруг products whitelist. Не покрывают workflow, WB client, funnel reports, Telegram, Google Sheets, ads/stocks. |
| Какие команды проверки должны проходить | DONE | По факту проходят `python -m pytest tests/test_stubs.py -q`, `python -m black --check .`, `python -m isort --check-only .` в текущей среде. |

## 8. Результаты проверок

Команды запускались после создания отчёта не как часть изменения Python-кода, а для фиксации требуемого результата аудита.

### `python -m pytest tests/test_stubs.py -q`

Статус: DONE / PASS.

```text
....                                                                     [100%]
4 passed in 0.03s
```

### `python -m black --check .`

Статус: DONE / PASS.

```text
All done! ✨ 🍰 ✨
13 files would be left unchanged.
```

### `python -m isort --check-only .`

Статус: DONE / PASS.

```text
Skipped 1 files
```

## 9. Что, вероятно, ушло в неправильный репозиторий `fm26-bot333` и требует повторного выполнения в `wb_morning_brief`

Обязательный список из задания плюс элементы, выявленные аудитом:

1. Google Sheets SELLERS: сейчас только hardcoded `get_sellers()`, без реального чтения из Google Sheets.
2. Google Sheets PRODUCTS: сейчас только hardcoded `get_products()`, без реального чтения из Google Sheets.
3. PRODUCTS whitelist: фильтр в коде есть и покрыт smoke-тестами, но источник PRODUCTS пока stub; нужно подключить реальное Google Sheets чтение и проверить контракт колонок.
4. `docs/CURRENT_STATE.md`: файл отсутствует.
5. `pytest` dependency: отсутствует в `requirements.txt`.
6. `black`/`isort` fixes/dependencies: форматирование в текущей среде проходит, но `black` и `isort` отсутствуют в `requirements.txt`; надо решить, должны ли они быть runtime/dev dependencies именно в этом проекте.
7. Google Sheets secrets/env в workflow: `GOOGLE_SHEETS_CREDENTIALS_JSON` и `GOOGLE_SHEETS_SPREADSHEET_ID` не передаются.
8. `gspread` и `google-auth`: отсутствуют в `requirements.txt`, что блокирует реальную Google Sheets интеграцию.
9. `TASKS` generation: `create_tasks()` является stub и не интегрирован.
10. `CHANGE_LOG` / `recentChanges` из Google Sheets: recentChanges logic есть, но источник hardcoded.
11. Stocks collector/analyzer: `app/collectors/stocks.py` и `app/analyzers/stocks_analyzer.py` отсутствуют.
12. Ads collector/analyzer: файлы есть, но оба stub и не получают/не анализируют реальные данные.
13. `all_problems` pipeline: переменная создаётся, но не используется; ads problems не попадают в Telegram, stocks pipeline отсутствует.
14. `app/analyzers/funnel_analyzer.py`: отсутствует, хотя часть funnel analyzer logic находится в `app/collectors/funnel.py`; стоит решить, нужен ли отдельный модуль или оставить текущую архитектуру.
15. `app/reports/pdf_report.py`: отсутствует.

## 10. Рекомендуемый порядок восстановления

1. Зафиксировать целевую архитектуру: оставить funnel-анализ в `app/collectors/funnel.py` или вынести в `app/analyzers/funnel_analyzer.py`; решить судьбу `pdf_report.py`.
2. Восстановить документацию состояния: добавить/обновить `docs/CURRENT_STATE.md` отдельно от этого аудита.
3. Подключить зависимости разработки и Google Sheets (`pytest`, `black`, `isort`, `gspread`, `google-auth`) в `requirements.txt` или отдельный dev requirements-файл, если такова стратегия проекта.
4. Реализовать Google Sheets client: чтение SELLERS, PRODUCTS, CHANGE_LOG; использовать `GOOGLE_SHEETS_CREDENTIALS_JSON` и `GOOGLE_SHEETS_SPREADSHEET_ID`.
5. Обновить workflow: передать Google Sheets secrets/env vars в шаг запуска.
6. Проверить и закрепить PRODUCTS whitelist на реальных данных из Google Sheets; сохранить существующие smoke-тесты и при необходимости добавить только явно запрошенные тесты.
7. Интегрировать `CHANGE_LOG` / `recentChanges` из Google Sheets в reports и Telegram вместо hardcoded данных.
8. Реализовать `TASKS` generation и определить, куда записываются задачи.
9. Реализовать Ads collector/analyzer и включить ads-проблемы в итоговый `all_problems` pipeline.
10. Реализовать Stocks collector/analyzer и добавить stocks-проблемы в `all_problems` pipeline.
11. Исправить отправку Telegram: передавать `all_problems`, а не только `funnel_problems`, после проверки формата проблем из всех источников.
12. Прогнать smoke-проверки: `python -m pytest tests/test_stubs.py -q`, `python -m black --check .`, `python -m isort --check-only .`.
