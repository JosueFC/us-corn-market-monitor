-- Fails when the newest Quick Stats pull is older than nass_max_age_days (rerun python -m ingest.nass).
select max(_pulled_at) as last_pull
from {{ source('raw', 'nass_quickstats') }}
having max(_pulled_at) < cast(current_timestamp as timestamp) - to_days({{ var('nass_max_age_days') }})
