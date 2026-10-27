-- One row per report and marketing year with the balance-sheet items as columns, plus the identity checks.
-- Grain: wasde_number, my_start_year.
{% set items = [
    'area_planted', 'area_harvested', 'yield_bu_per_acre',
    'beginning_stocks', 'production', 'imports', 'supply_total',
    'feed_residual', 'food_seed_industrial', 'ethanol_use', 'domestic_total',
    'exports', 'use_total', 'ending_stocks',
    'season_avg_price', 'season_avg_price_high', 'season_avg_price_low'
] %}
with mapped as (
    select s.*, m.item
    from {{ ref('stg_wasde__corn_us') }} s
    join {{ ref('wasde_attribute_map') }} m using (attribute_key)
    where not m.ignored
),
wide as (
    select
        wasde_number,
        my_start_year,
        any_value(market_year)  as market_year,
        any_value(release_date) as release_date,
        any_value(report_month) as report_month,
        -- Est. or Proj. as flagged on the ending stocks line
        max(case when item = 'ending_stocks' then status end) as status,
        {%- for item in items %}
        max(case when item = '{{ item }}' then value_num end) as {{ item }}{{ "," if not loop.last }}
        {%- endfor %}
    from mapped
    group by wasde_number, my_start_year
)
select
    *,
    ending_stocks / nullif(use_total, 0)                                      as stocks_to_use,
    beginning_stocks + production + imports - supply_total                    as supply_identity_gap,
    supply_total - use_total - ending_stocks                                  as balance_identity_gap,
    domestic_total + exports - use_total                                      as use_identity_gap,
    feed_residual + food_seed_industrial - domestic_total                     as domestic_identity_gap
from wide
