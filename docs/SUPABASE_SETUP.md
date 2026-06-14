# Supabase setup

This project is preparing to move WB Morning Brief storage from Google Sheets to Supabase/PostgreSQL.

## Create a Supabase project

1. Open the Supabase dashboard: <https://supabase.com/dashboard>.
2. Create a new project in the target organization.
3. Choose a project name, database password, and region.
4. Wait until Supabase finishes provisioning the project.

## Apply the database schema

1. In the Supabase dashboard, open the created project.
2. Go to **SQL Editor**.
3. Create a new query.
4. Copy the contents of [`../supabase/schema.sql`](../supabase/schema.sql) into the editor.
5. Run the query and confirm that the tables and indexes are created successfully.

The schema creates the initial storage tables for sellers, products, changes, daily funnel metrics, detected problems, tasks, advertising metrics, and stock snapshots.

## Required environment variables and secrets

The future Supabase storage implementation will need these values:

- `SUPABASE_URL` — Supabase project URL from **Project Settings → API**.
- `SUPABASE_SERVICE_ROLE_KEY` — service role key from **Project Settings → API**.

Keep `SUPABASE_SERVICE_ROLE_KEY` only in secure runtime secrets. Do not commit it to the repository and do not expose it in client-side code.

## Current application status

Supabase is not connected in the Python code yet. Google Sheets is being removed as the main storage, and the application temporarily uses `app/storage/stub_storage.py` as a stub storage layer.

## Next step

Implement `app/storage/supabase_storage.py` to read and write WB Morning Brief data through Supabase/PostgreSQL using the schema from `supabase/schema.sql`.
