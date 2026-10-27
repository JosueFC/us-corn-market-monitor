-- WASDE reports. Grain: one row per report (wasde_number). release_date is when the report became public.
select distinct
    wasde_number                                       as report_vintage_key,
    release_date,
    report_month,
    {{ marketing_year_start('release_date') }}         as release_marketing_year_start,
    source_file,
    is_reissue
from {{ ref('stg_wasde__corn_us') }}
