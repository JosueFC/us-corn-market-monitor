-- Quarterly US corn grain stocks (bushels) on 1 March, 1 June, 1 September and 1 December.
-- Grain: one row per stocks date and location (total, on farm, off farm).
select
    make_date(
        cast("year" as integer),
        case reference_period_desc
            when 'FIRST OF MAR' then 3 when 'FIRST OF JUN' then 6
            when 'FIRST OF SEP' then 9 when 'FIRST OF DEC' then 12 end,
        1)                                                         as stocks_date,
    case short_desc
        when 'CORN, GRAIN - STOCKS, MEASURED IN BU'            then 'total'
        when 'CORN, ON FARM, GRAIN - STOCKS, MEASURED IN BU'   then 'on_farm'
        when 'CORN, OFF FARM, GRAIN - STOCKS, MEASURED IN BU'  then 'off_farm'
    end                                                        as location,
    {{ nass_number('value') }}                                 as stocks_bu,
    {{ nass_suppression('value') }}                            as suppression_code,
    trim(value)                                                as value_as_published,
    unit_desc,
    _pulled_at
from {{ source('raw', 'nass_quickstats') }}
where query_name = 'stocks_national'
  and freq_desc = 'POINT IN TIME'
  and domain_desc = 'TOTAL'
  and short_desc in (
      'CORN, GRAIN - STOCKS, MEASURED IN BU',
      'CORN, ON FARM, GRAIN - STOCKS, MEASURED IN BU',
      'CORN, OFF FARM, GRAIN - STOCKS, MEASURED IN BU')
