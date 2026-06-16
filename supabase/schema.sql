create table if not exists sellers (
    id bigint primary key,
    seller_name text not null,
    cabinet_name text,
    responsible text,
    status text default 'active',
    wb_token_secret_name text,
    created_at timestamptz default now()
);

create table if not exists products (
    id bigserial primary key,
    seller_id bigint references sellers(id),
    nm_id bigint not null,
    vendor_code text,
    product_name text,
    brand text,
    abc text default 'UNKNOWN',
    status text default 'active',
    created_at timestamptz default now(),
    unique(seller_id, nm_id)
);

create table if not exists change_log (
    id bigserial primary key,
    seller_id bigint references sellers(id),
    nm_id bigint,
    change_date date not null,
    change_type text,
    description text,
    created_at timestamptz default now()
);

create table if not exists daily_funnel (
    id bigserial primary key,
    report_date date not null,
    seller_id bigint references sellers(id),
    nm_id bigint not null,
    vendor_code text,
    title text,
    brand text,
    open_count integer default 0,
    cart_count integer default 0,
    order_count integer default 0,
    order_sum numeric default 0,
    add_to_cart_percent numeric,
    cart_to_order_percent numeric,
    wb_stocks integer,
    mp_stocks integer,
    real_sellable_stock numeric,
    incoming_stock numeric,
    returning_stock numeric,
    ready_for_sale_stock numeric,
    acceptance_stock numeric,
    transit_stock numeric,
    stock_state text,
    avg_position numeric,
    position_delta numeric,
    visibility_score numeric,
    search_visibility_risk text,
    raw_json jsonb,
    created_at timestamptz default now(),
    unique(report_date, seller_id, nm_id)
);

create table if not exists problems (
    id bigserial primary key,
    report_date date not null,
    seller_id bigint references sellers(id),
    nm_id bigint,
    vendor_code text,
    title text,
    abc text,
    problem_type text,
    problem_label text,
    metric text,
    selected_value numeric,
    past_value numeric,
    baseline_type text,
    baseline_value numeric,
    dynamic_percent numeric,
    root_cause text,
    root_recommendation text,
    severity text,
    severity_score numeric,
    business_impact_score numeric,
    is_below_abc_threshold boolean default false,
    lost_orders numeric,
    lost_order_sum numeric,
    potential_revenue_loss numeric,
    potential_orders_loss numeric,
    impact_confidence text,
    blocked_revenue_per_day numeric,
    blocked_orders_per_day numeric,
    avg_position numeric,
    position_delta numeric,
    visibility_score numeric,
    search_visibility_risk text,
    real_sellable_stock numeric,
    incoming_stock numeric,
    returning_stock numeric,
    ready_for_sale_stock numeric,
    acceptance_stock numeric,
    transit_stock numeric,
    stock_state text,
    ads_traffic_share numeric,
    low_ads_ctr_flag boolean default false,
    high_cpc_flag boolean default false,
    low_ads_traffic_share_flag boolean default false,
    organic_traffic_share numeric,
    ads_orders_share numeric,
    organic_orders_share numeric,
    decline_source text,
    budget_waste_risk boolean default false,
    report_trust_score text,
    forecast_eta_hours numeric,
    days_until_oos numeric,
    forecast_confidence text,
    forecast_type text,
    forecast_message text,
    recommendation text,
    recent_changes text,
    created_at timestamptz default now()
);

create table if not exists tasks (
    id bigserial primary key,
    report_date date not null,
    seller_id bigint references sellers(id),
    nm_id bigint,
    vendor_code text,
    title text,
    problem_type text,
    priority text,
    action text,
    status text default 'Новая',
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    unique(report_date, seller_id, nm_id, problem_type)
);

create table if not exists ads_daily (
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
    orders_sum numeric default 0,
    drr numeric,
    raw_json jsonb,
    created_at timestamptz default now()
);

create table if not exists daily_ads_metrics (
    id bigint generated always as identity primary key,

    date date,
    report_date date,
    seller_id text,
    seller_name text,

    campaign_id bigint,
    campaign_name text,
    campaign_status text,
    campaign_type text,
    nm_id bigint,
    vendor_code text,
    title text,

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
    bid_delta numeric,
    ctr_delta numeric,
    cpc_delta numeric,
    drr_delta numeric,
    avg_position numeric,
    position_delta numeric,
    ads_root_cause text,
    ads_efficiency_score numeric,
    auction_temperature text,

    raw_json jsonb,
    created_at timestamp default now(),

    unique(report_date, seller_id, campaign_id, nm_id)
);

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
    last_stats_rows integer default 0,
    last_error_code text,
    consecutive_errors integer default 0,
    unique (seller_id, campaign_id)
);

create table if not exists stocks_daily (
    id bigserial primary key,
    report_date date not null,
    seller_id bigint references sellers(id),
    nm_id bigint,
    warehouse_name text,
    quantity integer default 0,
    raw_json jsonb,
    created_at timestamptz default now()
);

create index if not exists idx_products_seller_id_nm_id
    on products(seller_id, nm_id);

create index if not exists idx_daily_funnel_report_date_seller_id
    on daily_funnel(report_date, seller_id);

create index if not exists idx_problems_report_date_seller_id
    on problems(report_date, seller_id);

create index if not exists idx_tasks_report_date_seller_id_status
    on tasks(report_date, seller_id, status);

create index if not exists idx_ads_daily_report_date_seller_id
    on ads_daily(report_date, seller_id);

create index if not exists idx_daily_ads_metrics_report_seller
    on daily_ads_metrics(report_date, seller_id);

create index if not exists idx_daily_ads_metrics_campaign_nm
    on daily_ads_metrics(seller_id, campaign_id, nm_id, report_date desc);

create unique index if not exists daily_ads_metrics_unique_idx
    on daily_ads_metrics(report_date, seller_id, campaign_id, nm_id);

create index if not exists idx_ads_campaigns_cache_seller_last_seen
    on ads_campaigns_cache(seller_id, last_seen_at desc);

create index if not exists idx_ads_campaigns_cache_seller_last_stats
    on ads_campaigns_cache(seller_id, last_stats_at asc nulls first);

create index if not exists idx_stocks_daily_report_date_seller_id
    on stocks_daily(report_date, seller_id);

create table if not exists daily_qbiki_metrics (
    id bigint generated always as identity primary key,
    date date,
    seller_name text,
    nm_id bigint,
    vendor_code text,
    title text,

    orders_per_1000_impressions numeric,
    organic_cr numeric,
    ads_cr numeric,
    ads_orders numeric,
    ads_impressions numeric,
    ads_ctr numeric,
    ads_clicks numeric,
    cart_conversion numeric,
    order_conversion numeric,
    avg_ad_bid numeric,
    ad_profit_per_order numeric,
    cpo numeric,
    drr numeric,
    clean_drr numeric,
    clean_margin numeric,
    clean_margin_organic numeric,
    clean_margin_ads numeric,
    roi numeric,
    wb_stock numeric,
    days_of_stock numeric,

    created_at timestamp default now()
);
create table if not exists api_coverage_daily (
    id bigint generated always as identity primary key,
    report_date date,
    seller_name text,
    nm_id bigint,
    vendor_code text,
    title text,

    in_cards_api boolean default false,
    in_products_catalog boolean default false,
    in_funnel_api boolean default false,
    in_ads_api boolean default false,
    in_supplies_api boolean default false,
    in_problems boolean default false,
    in_telegram_top boolean default false,

    has_funnel_metrics boolean default false,
    has_ads_metrics boolean default false,
    has_supply_metrics boolean default false,
    has_forecast boolean default false,
    has_business_impact boolean default false,

    funnel_fields_filled integer,
    ads_fields_filled integer,
    supply_fields_filled integer,
    problem_count integer,
    ads_problem_count integer,
    funnel_problem_count integer,
    ads_campaign_count integer,
    ads_rows_count integer,

    created_at timestamp default now()
);

create unique index if not exists api_coverage_daily_unique_idx
on api_coverage_daily (report_date, seller_name, nm_id);

alter table if exists public.sellers enable row level security;
alter table if exists public.products enable row level security;
alter table if exists public.change_log enable row level security;
alter table if exists public.tasks enable row level security;
alter table if exists public.ads_daily enable row level security;
alter table if exists public.stocks_daily enable row level security;
alter table if exists public.problems enable row level security;
alter table if exists public.daily_funnel enable row level security;
alter table if exists public.daily_ads_metrics enable row level security;
alter table if exists public.api_coverage_daily enable row level security;
alter table if exists public.daily_qbiki_metrics enable row level security;
