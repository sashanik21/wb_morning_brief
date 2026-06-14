alter table problems
    add column if not exists potential_revenue_loss numeric,
    add column if not exists potential_orders_loss numeric,
    add column if not exists impact_confidence text,
    add column if not exists blocked_revenue_per_day numeric,
    add column if not exists blocked_orders_per_day numeric;
