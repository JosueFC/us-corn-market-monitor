-- Quarterly stocks pivoted to total, on-farm and off-farm columns. Grain: stocks_date.
select
    stocks_date,
    max(case when location = 'total'    then stocks_bu end) as total_stocks_bu,
    max(case when location = 'on_farm'  then stocks_bu end) as on_farm_stocks_bu,
    max(case when location = 'off_farm' then stocks_bu end) as off_farm_stocks_bu
from {{ ref('stg_nass__stocks') }}
group by stocks_date
