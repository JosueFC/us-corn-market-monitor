-- Every report and marketing year must carry the items the identities and the model features rely on.
select wasde_number, my_start_year
from {{ ref('int_wasde_balance') }}
where beginning_stocks is null or production is null or imports is null
   or supply_total is null or use_total is null or ending_stocks is null
