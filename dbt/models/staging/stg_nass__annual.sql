-- Annual corn acreage, yield and production, national and by state, including NASS's in-season
-- forecasts and acreage reports (reference_period_desc: YEAR is the estimate, others are forecasts).
-- Only the TOTAL domain is kept; the raw table also holds chemical, fertilizer and practice surveys.
-- Grain: one row per state, crop year, reference period and measure.
select
    state_fips_code                                  as state_key,
    state_alpha,
    state_name,
    agg_level_desc = 'NATIONAL'                      as is_national,
    cast("year" as integer)                          as crop_year,
    reference_period_desc,
    case short_desc
        when 'CORN - ACRES PLANTED'                         then 'acres_planted'
        when 'CORN, GRAIN - ACRES HARVESTED'                then 'acres_harvested'
        when 'CORN, GRAIN - YIELD, MEASURED IN BU / ACRE'   then 'yield_bu_per_acre'
        when 'CORN, GRAIN - PRODUCTION, MEASURED IN BU'     then 'production_bu'
    end                                              as measure,
    {{ nass_number('value') }}                       as value_num,
    {{ nass_suppression('value') }}                  as suppression_code,
    trim(value)                                      as value_as_published,
    unit_desc,
    _pulled_at
from {{ source('raw', 'nass_quickstats') }}
where query_name in ('annual_national', 'annual_state')
  and freq_desc = 'ANNUAL'
  and domain_desc = 'TOTAL'
  and short_desc in (
      'CORN - ACRES PLANTED',
      'CORN, GRAIN - ACRES HARVESTED',
      'CORN, GRAIN - YIELD, MEASURED IN BU / ACRE',
      'CORN, GRAIN - PRODUCTION, MEASURED IN BU')
