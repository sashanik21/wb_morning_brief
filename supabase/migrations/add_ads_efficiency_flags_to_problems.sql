alter table problems
    add column if not exists low_ads_ctr_flag boolean default false,
    add column if not exists high_cpc_flag boolean default false,
    add column if not exists low_ads_traffic_share_flag boolean default false;
