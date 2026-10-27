-- Fails when the newest WASDE report in the database was released more than wasde_max_age_days ago.
select max(release_date) as last_release
from {{ ref('stg_wasde__corn_us') }}
having max(release_date) < current_date - to_days({{ var('wasde_max_age_days') }})
