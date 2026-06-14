# Supabase migrations

Use this guide when GitHub Actions starts writing fields that are missing from the Supabase table schema, for example when the job logs a schema cache warning for the `problems` table.

## How to run a migration in Supabase

1. Open the Supabase project dashboard.
2. In the left navigation, open **SQL Editor**.
3. Click **New query**.
4. Copy the SQL from the required file in `supabase/migrations/`.
   For the `problems` baseline and severity fields, use:
   `supabase/migrations/add_problem_baseline_severity_fields.sql`.
5. Paste the SQL into the editor and click **Run**.

The migration is safe to run more than once because it uses `add column if not exists`.

## After running the migration

After the SQL finishes successfully, restart the failed GitHub Actions workflow so the next Supabase save uses the refreshed table schema.

The expected successful workflow log contains a line like:

```text
SUPABASE SAVE PROBLEMS: N rows
```

There should be no warning similar to:

```text
Could not find the 'baseline_type' column of 'problems' in the schema cache
```
