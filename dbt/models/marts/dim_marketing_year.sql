-- Corn marketing years. Grain: one row per marketing year.
select
    my_start_year                                              as marketing_year_key,
    {{ marketing_year_label('my_start_year') }}                as marketing_year_label,
    make_date(my_start_year, 9, 1)                             as start_date,
    make_date(my_start_year + 1, 8, 31)                        as end_date
from (select distinct marketing_year_start as my_start_year from {{ ref('dim_date') }})
