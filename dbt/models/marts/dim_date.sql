-- Calendar with the corn marketing year (September to August). Grain: one row per day.
-- The marketing year is identified by the calendar year it starts in; 2025/26 starts on 1 September 2025.
with days as (
    select cast(d as date) as date_key
    from generate_series(date '1975-01-01', date '2035-12-31', interval 1 day) as t(d)
)
select
    date_key,
    year(date_key)                                                  as calendar_year,
    month(date_key)                                                 as month_number,
    strftime(date_key, '%b')                                        as month_name,
    strftime(date_key, '%Y-%m')                                     as year_month,
    cast(date_trunc('month', date_key) as date)                     as month_start_date,
    quarter(date_key)                                               as calendar_quarter,
    {{ marketing_year_start('date_key') }}                          as marketing_year_start,
    {{ marketing_year_label(marketing_year_start('date_key')) }}     as marketing_year_label,
    (month(date_key) + 3) % 12 + 1                                  as marketing_year_month_number,
    day(date_key) = 1                                               as is_month_start
from days
