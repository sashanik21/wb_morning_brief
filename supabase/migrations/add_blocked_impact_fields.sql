alter table problems
    add column if not exists blocked_orders_per_day numeric,
    add column if not exists blocked_revenue_per_day numeric;
