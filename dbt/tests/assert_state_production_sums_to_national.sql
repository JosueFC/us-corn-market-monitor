-- State production must add up to the national figure (final annual estimates).
-- Fails for a crop year where the difference exceeds production_sum_tolerance (a fraction),
-- or where fewer than five states were summed (the sum would not be a meaningful check).
select *
from {{ ref('int_production_reconciliation') }}
where reference_period_desc = 'YEAR'
  and (relative_difference > {{ var('production_sum_tolerance') }} or n_states < 5)
