alter table problems
    add column if not exists severity text,
    add column if not exists severity_score numeric,
    add column if not exists lost_orders numeric,
    add column if not exists lost_order_sum numeric;
