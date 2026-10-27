-- US corn balance sheet and price projection as published in each WASDE report.
-- Grain: one row per report, attribute and marketing year.
-- Selects the US corn table, normalizes attribute names and dates, and keeps one file version per report
-- (a re-issued "-V2" file replaces the original).
with parsed as (
    select
        try_cast(wasde_number as integer)                             as wasde_number,
        source_file,
        member,
        {{ parse_date('release_date') }}                              as release_date,
        coalesce(
            cast(try_strptime(trim(report_date), '%B %Y') as date),
            cast(try_strptime(trim(report_date), '%b %Y') as date))   as report_month,
        trim(attribute)                                               as attribute_label,
        lower(trim(attribute))                                        as attribute_key,
        trim(market_year)                                             as market_year,
        try_cast(substr(trim(market_year), 1, 4) as integer)          as my_start_year,
        nullif(trim(proj_est_flag), '')                               as status,
        {{ nass_number('value') }}                                    as value_num,
        trim(value)                                                   as value_as_published,
        trim(unit)                                                    as unit
    from {{ source('raw', 'wasde') }}
    where trim(commodity) = 'Corn'
      and trim(region) = 'United States'
      and trim(report_title) = 'U.S. Feed Grain and Corn Supply and Use'
),
chosen_file as (
    select wasde_number, source_file, member
    from (
        select
            wasde_number, source_file, member,
            row_number() over (
                partition by wasde_number
                order by (lower(source_file) like '%v2%') desc, source_file desc, member desc) as rn
        from (select distinct wasde_number, source_file, member from parsed)
    )
    where rn = 1
)
select
    p.wasde_number,
    p.source_file,
    lower(p.source_file) like '%v2%'  as is_reissue,
    p.release_date,
    coalesce(p.report_month, date_trunc('month', p.release_date)) as report_month,
    p.attribute_label,
    p.attribute_key,
    p.market_year,
    p.my_start_year,
    p.status,
    p.value_num,
    p.value_as_published,
    p.unit
from parsed p
join chosen_file c
  on p.wasde_number = c.wasde_number and p.source_file = c.source_file and p.member = c.member
