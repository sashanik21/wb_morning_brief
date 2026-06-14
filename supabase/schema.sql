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
    dynamic_percent numeric,
    root_cause text,
    root_recommendation text,
    severity_score numeric,
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

create index if not exists idx_stocks_daily_report_date_seller_id
    on stocks_daily(report_date, seller_id);
