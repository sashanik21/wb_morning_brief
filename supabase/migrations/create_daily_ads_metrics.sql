create table if not exists daily_ads_metrics (
    id bigint generated always as identity primary key,

    date date,
    report_date date,
    seller_id bigint references sellers(id),

    campaign_id bigint,
    campaign_name text,
    nm_id bigint,

    impressions numeric,
    clicks numeric,
    ctr numeric,

    cpc numeric,
    cpm numeric,

    spend numeric,
    orders_count numeric,
    orders numeric,
    revenue numeric,

    drr numeric,

    bid numeric,
    avg_position numeric,

    raw_json jsonb,
    created_at timestamp default now(),

    unique(report_date, seller_id, campaign_id, nm_id)
);

create index if not exists idx_daily_ads_metrics_report_seller
    on daily_ads_metrics(report_date, seller_id);

create index if not exists idx_daily_ads_metrics_campaign_nm
    on daily_ads_metrics(seller_id, campaign_id, nm_id, report_date desc);

create index if not exists idx_daily_ads_metrics_date_campaign_nm
    on daily_ads_metrics(date, campaign_id, nm_id);
