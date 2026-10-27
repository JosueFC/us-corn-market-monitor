{{ config(severity='warn') }}
select report_vintage_key, report_month, release_date
from {{ ref('dim_report_vintage') }}
where date_trunc('month', release_date) <> report_month
