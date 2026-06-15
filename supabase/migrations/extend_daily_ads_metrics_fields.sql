alter table daily_ads_metrics
    add column if not exists seller_id text,
    add column if not exists seller_name text,
    add column if not exists campaign_name text,
    add column if not exists campaign_status text,
    add column if not exists campaign_type text,
    add column if not exists vendor_code text,
    add column if not exists title text,
    add column if not exists bid_delta numeric,
    add column if not exists ctr_delta numeric,
    add column if not exists cpc_delta numeric,
    add column if not exists drr_delta numeric,
    add column if not exists position_delta numeric,
    add column if not exists ads_root_cause text,
    add column if not exists ads_efficiency_score numeric,
    add column if not exists auction_temperature text;

alter table problems
    add column if not exists budget_waste_risk boolean default false,
    add column if not exists report_trust_score text,
    add column if not exists forecast_eta_hours numeric,
    add column if not exists blocked_orders_per_day numeric,
    add column if not exists blocked_revenue_per_day numeric;
