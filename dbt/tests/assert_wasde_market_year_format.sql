-- Marketing years look like 2024/25 and the second part is the year after the first.
select wasde_number, market_year
from {{ ref('stg_wasde__corn_us') }}
where not regexp_full_match(market_year, '[0-9]{4}/[0-9]{2}')
   or cast(substr(market_year, 6, 2) as integer) <> (my_start_year + 1) % 100
