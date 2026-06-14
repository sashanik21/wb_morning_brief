alter table daily_funnel
    add column if not exists real_sellable_stock numeric,
    add column if not exists incoming_stock numeric,
    add column if not exists returning_stock numeric,
    add column if not exists ready_for_sale_stock numeric,
    add column if not exists acceptance_stock numeric,
    add column if not exists transit_stock numeric,
    add column if not exists stock_state text;

alter table problems
    add column if not exists real_sellable_stock numeric,
    add column if not exists incoming_stock numeric,
    add column if not exists returning_stock numeric,
    add column if not exists ready_for_sale_stock numeric,
    add column if not exists acceptance_stock numeric,
    add column if not exists transit_stock numeric,
    add column if not exists stock_state text;
