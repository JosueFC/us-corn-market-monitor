-- States with corn statistics, plus the national total. Grain: one row per state_key (FIPS code; '99' = US).
select
    state_key,
    any_value(state_alpha)                              as state_code,
    any_value(state_name)                               as state_name,
    bool_or(is_national)                                as is_national
from (
    select state_key, state_alpha, state_name, is_national from {{ ref('stg_nass__price_monthly') }}
    union all
    select state_key, state_alpha, state_name, is_national from {{ ref('stg_nass__annual') }}
    union all
    select state_key, state_alpha, state_name, is_national from {{ ref('stg_nass__condition') }}
)
where state_key is not null
group by state_key
