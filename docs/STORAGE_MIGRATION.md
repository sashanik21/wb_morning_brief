# Storage migration: Google Sheets → Supabase

Google Sheets is no longer the primary storage layer for the Morning Brief pipeline.
The application now reads operational storage data through `app/storage/` instead of
using Google Sheets directly for sellers, products, change log, tasks, or daily
reports.

## Current temporary storage

The project temporarily uses `app/storage/stub_storage.py` as the active storage
implementation. It contains local stub data for:

- sellers;
- products;
- change_log;
- tasks fallback.

This keeps the current pipeline runnable while the production storage migration is
prepared.

## Next stage

The next stage is to implement `app/storage/supabase_storage.py` and connect it to
Supabase/PostgreSQL. This PR intentionally does not connect the Supabase API yet.
The Supabase module currently contains function stubs that raise
`NotImplementedError` until the database schema and client integration are ready.

## Required Supabase tables

The Supabase/PostgreSQL storage should include these tables:

- `sellers`
- `products`
- `change_log`
- `daily_funnel`
- `problems`
- `tasks`
- `ads_daily`
- `stocks_daily`
