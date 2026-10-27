-- WASDE balance-sheet identities, within wasde_identity_tolerance million bushels (items are rounded):
--   beginning stocks + production + imports = total supply
--   total supply = total use + ending stocks
--   domestic use + exports = total use
select wasde_number, my_start_year, supply_identity_gap, balance_identity_gap, use_identity_gap
from {{ ref('int_wasde_balance') }}
where abs(supply_identity_gap)  > {{ var('wasde_identity_tolerance') }}
   or abs(balance_identity_gap) > {{ var('wasde_identity_tolerance') }}
   or abs(use_identity_gap)     > {{ var('wasde_identity_tolerance') }}
