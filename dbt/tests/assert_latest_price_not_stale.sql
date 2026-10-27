-- Fails when the newest national monthly price is more than price_max_months_behind months old.
select max(price_month) as latest_price_month
from {{ ref('stg_nass__price_monthly') }}
where is_national
having max(price_month) < cast(date_trunc('month', current_date) as date) - to_months({{ var('price_max_months_behind') }})
