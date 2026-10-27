-- Units are consistent per measure and values are plausible (catches bushels versus million bushels,
-- acres versus thousand acres, cents versus dollars).
select state_key, crop_year, reference_period_desc, measure, unit_desc, value_num
from {{ ref('stg_nass__annual') }}
where value_num is not null
  and (
      (measure in ('acres_planted', 'acres_harvested') and (unit_desc <> 'ACRES' or value_num < 0 or value_num > 120000000))
   or (measure = 'yield_bu_per_acre' and (unit_desc <> 'BU / ACRE' or value_num < 20 or value_num > 300))
   or (measure = 'production_bu' and (unit_desc <> 'BU' or value_num < 0 or value_num > 20000000000))
  )
