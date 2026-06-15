alter table daily_ads_metrics
    add column if not exists raw_json jsonb;

alter table problems
    add column if not exists impact_confidence text;
