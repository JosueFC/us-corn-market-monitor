-- There should be one report per month between the first and the last. A missing month is allowed only if it
-- is listed in seeds/known_wasde_gaps.csv with a reason.
with months as (
    select cast(m as date) as report_month
    from generate_series(
        (select min(report_month) from {{ ref('dim_report_vintage') }}),
        (select max(report_month) from {{ ref('dim_report_vintage') }}),
        interval 1 month) as t(m)
)
select months.report_month
from months
left join {{ ref('dim_report_vintage') }} v using (report_month)
left join {{ ref('known_wasde_gaps') }} g using (report_month)
where v.report_month is null and g.report_month is null
