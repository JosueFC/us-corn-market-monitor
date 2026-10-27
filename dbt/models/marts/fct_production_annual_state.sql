-- Final annual corn estimates by state (national total excluded so sums are safe). Grain: state_key, crop_year.
select
    state_key,
    crop_year                                  as marketing_year_key,
    acres_planted,
    acres_harvested,
    yield_bu_per_acre,
    production_bu
from {{ ref('int_nass_annual_final') }}
where not is_national
