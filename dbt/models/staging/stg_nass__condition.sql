-- Weekly corn condition ratings: percent of planted acreage in each class.
-- Grain: one row per state (national = 'US'), week ending date and rating class.
select
    state_fips_code                                  as state_key,
    state_alpha,
    state_name,
    agg_level_desc = 'NATIONAL'                      as is_national,
    cast("year" as integer)                          as crop_year,
    try_cast(replace(reference_period_desc, 'WEEK #', '') as integer) as week_number,
    try_cast(week_ending as date)                    as week_ending,
    lower(replace(short_desc, 'CORN - CONDITION, MEASURED IN PCT ', '')) as rating_class,
    {{ nass_number('value') }}                       as pct_of_acres,
    {{ nass_suppression('value') }}                  as suppression_code,
    trim(value)                                      as value_as_published,
    unit_desc,
    _pulled_at
from {{ source('raw', 'nass_quickstats') }}
where query_name in ('condition_national', 'condition_state')
  and freq_desc = 'WEEKLY'
  and domain_desc = 'TOTAL'
  and short_desc like 'CORN - CONDITION, MEASURED IN PCT %'
