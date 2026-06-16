alter table ads_campaigns_cache
    add column if not exists last_stats_rows integer default 0,
    add column if not exists last_error_code text,
    add column if not exists consecutive_errors integer default 0;
