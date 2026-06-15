alter table daily_ads_metrics
    add column if not exists report_date date,
    add column if not exists seller_id text,
    add column if not exists campaign_id bigint,
    add column if not exists nm_id bigint;

create unique index if not exists daily_ads_metrics_unique_idx
on daily_ads_metrics (report_date, seller_id, campaign_id, nm_id);

alter table problems
    add column if not exists potential_orders_loss numeric,
    add column if not exists potential_revenue_loss numeric,
    add column if not exists impact_confidence text,
    add column if not exists blocked_orders_per_day numeric,
    add column if not exists blocked_revenue_per_day numeric;
