-- Final annual estimates (reference period YEAR) pivoted to one row per state and crop year.
-- Grain: state_key, crop_year. Includes the national row (is_national).
select
    state_key,
    any_value(state_alpha)  as state_alpha,
    any_value(state_name)   as state_name,
    bool_or(is_national)    as is_national,
    crop_year,
    max(case when measure = 'acres_planted'      then value_num end) as acres_planted,
    max(case when measure = 'acres_harvested'    then value_num end) as acres_harvested,
    -- a yield of 0 on NASS's "OTHER STATES" row means not defined, so it is left empty
    max(case when measure = 'yield_bu_per_acre' and not (value_num = 0 and state_name like 'OTHER%')
             then value_num end) as yield_bu_per_acre,
    max(case when measure = 'production_bu'      then value_num end) as production_bu
from {{ ref('stg_nass__annual') }}
where reference_period_desc = 'YEAR'
group by state_key, crop_year
