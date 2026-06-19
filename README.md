# wb_morning_brief

## Streamlit Dashboard

Executive Dashboard читает данные из Supabase и ничего не изменяет.

### Переменные окружения

```bash
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_KEY="your-supabase-key"
```

Если в окружении уже используется `SUPABASE_SERVICE_ROLE_KEY` или `SUPABASE_ANON_KEY`, Dashboard также сможет использовать их как fallback для ключа.

### Запуск

```bash
streamlit run dashboard/app.py
```
