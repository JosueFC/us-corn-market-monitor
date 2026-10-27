-- Monthly corn price received, national and by state. Grain: state_key, price_month.
-- Prices are not additive across states; the national series is the row with the US state key.
-- available_date_* say when each price counts as public for point-in-time backtests:
--   lag      : the last day of the month `price_availability_lag_months` after the price month (assumption)
--   revision : the date NASS loaded the value now in Quick Stats (strict: the revised figure's own date)
select
    state_key,
    price_month                                                                   as price_month_key,
    price_usd_per_bu,
    suppression_code,
    nass_load_time,
    last_day(price_month + to_months({{ var('price_availability_lag_months') }})) as available_date_lag,
    cast(nass_load_time as date)                                                  as available_date_revision
from {{ ref('stg_nass__price_monthly') }}
