create table if not exists ads_bid_history (
    id bigserial primary key,
    seller_name text,
    campaign_id bigint not null,
    nm_id bigint,
    report_date date not null,
    bid_type text,
    payment_type text,
    search_bid numeric,
    recommendations_bid numeric,
    campaign_status integer,
    campaign_updated_at timestamptz,
    created_at timestamptz default now(),
    unique (campaign_id, nm_id, report_date)
);
