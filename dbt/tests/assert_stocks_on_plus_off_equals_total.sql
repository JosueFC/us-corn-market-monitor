{{ config(severity='warn') }}
select stocks_date, total_stocks_bu, on_farm_stocks_bu + off_farm_stocks_bu as on_plus_off
from {{ ref('int_stocks_quarterly') }}
where abs(on_farm_stocks_bu + off_farm_stocks_bu - total_stocks_bu) > 0.001 * total_stocks_bu
