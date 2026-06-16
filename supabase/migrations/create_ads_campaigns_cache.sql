create table if not exists ads_campaigns_cache (
    id bigint generated always as identity primary key,
    seller_id text,
    campaign_id bigint,
    campaign_name text,
    campaign_status text,
    campaign_type text,
    last_seen_at timestamp default now(),
    raw_json jsonb,
    last_stats_at timestamp,
    last_stats_status text,
    unique (seller_id, campaign_id)
);

alter table ads_campaigns_cache
    add column if not exists last_stats_at timestamp,
    add column if not exists last_stats_status text;

create index if not exists idx_ads_campaigns_cache_seller_last_seen
    on ads_campaigns_cache(seller_id, last_seen_at desc);

create index if not exists idx_ads_campaigns_cache_seller_last_stats
    on ads_campaigns_cache(seller_id, last_stats_at asc nulls first);
