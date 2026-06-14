alter table if exists daily_funnel
    add column if not exists avg_position numeric,
    add column if not exists position_delta numeric,
    add column if not exists visibility_score numeric,
    add column if not exists search_visibility_risk text;

alter table if exists problems
    add column if not exists avg_position numeric,
    add column if not exists position_delta numeric,
    add column if not exists visibility_score numeric,
    add column if not exists search_visibility_risk text,
    add column if not exists root_cause text;
