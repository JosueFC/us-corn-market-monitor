{{ config(severity='warn') }}
-- Same check for NASS's in-season forecasts. A warning only: the forecast tables may list the major states
-- and group the rest, which would not add up to the national figure.
select *
from {{ ref('int_production_reconciliation') }}
where reference_period_desc <> 'YEAR'
  and relative_difference > {{ var('production_sum_tolerance') }}
