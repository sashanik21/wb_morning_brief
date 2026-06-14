alter table if exists problems
    add column if not exists budget_waste_risk boolean default false,
    add column if not exists report_trust_score text,
    add column if not exists forecast_eta_hours numeric;
