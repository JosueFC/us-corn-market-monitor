-- Monthly corn price received by farmers ($/bu), national and by state.
-- Grain: one row per state and month. The national series has state_alpha = 'US'.
-- Quick Stats serves the latest revision of each month; nass_load_time says when that value was loaded.
select
    state_fips_code                                              as state_key,
    state_alpha,
    state_name,
    agg_level_desc = 'NATIONAL'                                  as is_national,
    make_date(cast("year" as integer), {{ month_number('reference_period_desc') }}, 1) as price_month,
    {{ nass_number('value') }}                                   as price_usd_per_bu,
    {{ nass_suppression('value') }}                              as suppression_code,
    trim(value)                                                  as value_as_published,
    unit_desc,
    try_cast(load_time as timestamp)                             as nass_load_time,
    _pulled_at
from {{ source('raw', 'nass_quickstats') }}
where query_name in ('price_received_national', 'price_received_state')
  and short_desc = 'CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU'
  and freq_desc = 'MONTHLY'
  and domain_desc = 'TOTAL'
