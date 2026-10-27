-- US corn stocks on the first of March, June, September and December. Grain: stocks_date.
select
    stocks_date                   as stocks_date_key,
    total_stocks_bu,
    on_farm_stocks_bu,
    off_farm_stocks_bu
from {{ ref('int_stocks_quarterly') }}
