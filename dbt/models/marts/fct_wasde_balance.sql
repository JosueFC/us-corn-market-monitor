-- US corn balance sheet as published in each WASDE report. Grain: report_vintage_key, marketing_year_key.
-- Quantities are million bushels; area in million acres; yield in bushels per acre; prices in $/bu.
-- status: Est. (estimate) or Proj. (projection) as flagged by USDA in that report.
select
    wasde_number                  as report_vintage_key,
    my_start_year                 as marketing_year_key,
    status,
    area_planted,
    area_harvested,
    yield_bu_per_acre,
    beginning_stocks,
    production,
    imports,
    supply_total,
    feed_residual,
    food_seed_industrial,
    ethanol_use,
    domestic_total,
    exports,
    use_total,
    ending_stocks,
    stocks_to_use,
    season_avg_price,
    season_avg_price_high,
    season_avg_price_low,
    supply_identity_gap,
    balance_identity_gap
from {{ ref('int_wasde_balance') }}
