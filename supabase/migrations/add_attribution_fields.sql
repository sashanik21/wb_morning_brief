alter table problems
    add column if not exists ads_traffic_share numeric,
    add column if not exists organic_traffic_share numeric,
    add column if not exists ads_orders_share numeric,
    add column if not exists organic_orders_share numeric,
    add column if not exists decline_source text;
