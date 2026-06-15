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

    created_at timestamp default now()
);

create unique index if not exists api_coverage_daily_unique_idx
on api_coverage_daily (report_date, seller_name, nm_id);
