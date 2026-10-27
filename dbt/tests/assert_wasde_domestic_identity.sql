{{ config(severity='warn') }}
-- Feed and residual + food, seed and industrial should equal domestic use. A warning only, because other
-- minor domestic items may appear in some reports.
select wasde_number, my_start_year, domestic_identity_gap
from {{ ref('int_wasde_balance') }}
where abs(domestic_identity_gap) > {{ var('wasde_identity_tolerance') }}
