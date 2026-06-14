create table if not exists daily_ads_metrics (
    id bigserial primary key,
    report_date date not null,
    seller_id bigint references sellers(id),
    campaign_id bigint,
    campaign_name text,
    nm_id bigint,
    impressions integer default 0,
    clicks integer default 0,
    ctr numeric,
    cpc numeric,
    cpm numeric,
    spend numeric default 0,
    orders integer default 0,
    revenue numeric default 0,
    drr numeric,
    bid numeric,
    avg_position numeric,
    raw_json jsonb,
    created_at timestamptz default now(),
    unique(report_date, seller_id, campaign_id, nm_id)
);

create index if not exists idx_daily_ads_metrics_report_seller
    on daily_ads_metrics(report_date, seller_id);

create index if not exists idx_daily_ads_metrics_campaign_nm
    on daily_ads_metrics(seller_id, campaign_id, nm_id, report_date desc);
