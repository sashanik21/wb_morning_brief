# WB Morning Brief Architecture

Проект переходит от генерации PNG dashboard к data-first архитектуре, где основным источником для аналитики становится Supabase.

## Текущий поток данных

```text
WB API
↓
Collectors
↓
Supabase
↓
Analyzers
↓
Telegram Morning Brief
```

## Компоненты

- **WB API** — источник данных по воронке продаж, товарам, рекламе и другим бизнес-событиям Wildberries.
- **Collectors** — слой получения и нормализации данных из WB API. Коллекторы подготавливают данные для сохранения и последующей аналитики.
- **Supabase** — центральное хранилище операционных и аналитических данных: snapshots, problems, tasks и связанные сущности.
- **Analyzers** — слой анализа данных из collectors и Supabase: выявление проблем, root-cause анализ, построение задач и summary-показателей.
- **Telegram Morning Brief** — текстовый утренний бриф для Telegram. Он отправляет только текстовую сводку без PNG-изображений и dashboard captions.

## Что удалено из архитектуры

PNG dashboard generation больше не является частью проекта. Проект не генерирует dashboard images, не сохраняет `dashboard_YYYY_MM_DD.png` и не отправляет изображения в Telegram.

## Следующий этап

Следующий этап развития — **web analytics dashboard поверх Supabase**.

Планируемый dashboard должен читать данные из Supabase напрямую или через отдельный API-слой и предоставлять интерактивную аналитику без генерации статичных PNG-файлов.
