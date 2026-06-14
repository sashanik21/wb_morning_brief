alter table problems
    add column if not exists days_until_oos numeric,
    add column if not exists forecast_confidence text,
    add column if not exists forecast_type text,
    add column if not exists forecast_message text;
