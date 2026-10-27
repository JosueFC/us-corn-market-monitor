-- National marketing-year average price received ($/bu). NASS labels the marketing year by the
-- calendar year it starts in (year 2025 = September 2025 to August 2026).
-- Grain: one row per marketing year.
select
    cast("year" as integer)                as my_start_year,
    {{ nass_number('value') }}             as price_usd_per_bu,
    {{ nass_suppression('value') }}        as suppression_code,
    trim(value)                            as value_as_published,
    unit_desc,
    try_cast(load_time as timestamp)       as nass_load_time,
    _pulled_at
from {{ source('raw', 'nass_quickstats') }}
where query_name = 'price_received_national'
  and short_desc = 'CORN, GRAIN - PRICE RECEIVED, MEASURED IN $ / BU'
  and freq_desc = 'ANNUAL'
  and reference_period_desc = 'MARKETING YEAR'
  and domain_desc = 'TOTAL'
