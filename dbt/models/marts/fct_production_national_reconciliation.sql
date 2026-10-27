-- National production against the sum of states, for the data notes page. Grain: crop_year (final estimates).
select
    crop_year                  as marketing_year_key,
    national_production_bu,
    sum_of_states_bu,
    n_states,
    difference_bu,
    relative_difference
from {{ ref('int_production_reconciliation') }}
where reference_period_desc = 'YEAR'
