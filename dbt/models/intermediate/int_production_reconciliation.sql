-- Compares the national production figure with the sum of the state figures for the same crop year and
-- reference period. Grain: crop_year, reference_period_desc.
with national as (
    select crop_year, reference_period_desc, value_num as national_production_bu
    from {{ ref('stg_nass__annual') }}
    where is_national and measure = 'production_bu' and value_num is not null
),
states as (
    select
        crop_year,
        reference_period_desc,
        sum(value_num) as sum_of_states_bu,
        count(*)       as n_states
    from {{ ref('stg_nass__annual') }}
    where not is_national and measure = 'production_bu' and value_num is not null
    group by crop_year, reference_period_desc
)
select
    n.crop_year,
    n.reference_period_desc,
    n.national_production_bu,
    s.sum_of_states_bu,
    s.n_states,
    s.sum_of_states_bu - n.national_production_bu                                   as difference_bu,
    abs(s.sum_of_states_bu - n.national_production_bu) / nullif(n.national_production_bu, 0) as relative_difference
from national n
join states s using (crop_year, reference_period_desc)
