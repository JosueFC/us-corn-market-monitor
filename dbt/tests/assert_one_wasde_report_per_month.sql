select report_month, count(*) as n_reports
from {{ ref('dim_report_vintage') }}
group by report_month
having count(*) > 1
