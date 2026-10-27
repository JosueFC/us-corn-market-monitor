-- One row per marketing year joining the NASS marketing-year price with the latest WASDE balance sheet
-- that includes that year. Grain: marketing_year_key.
-- latest_wasde_* come from the most recent report in the files; for finished years that is USDA's last
-- published estimate, which can differ from what was known at the time (use fct_wasde_balance for that).
with latest_wasde as (
    select *
    from (
        select
            *,
            row_number() over (partition by my_start_year order by wasde_number desc) as rn
        from {{ ref('int_wasde_balance') }}
    )
    where rn = 1
)
select
    p.my_start_year                    as marketing_year_key,
    p.price_usd_per_bu                 as nass_marketing_year_price,
    w.wasde_number                     as latest_report_vintage_key,
    w.status                           as latest_wasde_status,
    w.production                       as latest_wasde_production,
    w.use_total                        as latest_wasde_use_total,
    w.ending_stocks                    as latest_wasde_ending_stocks,
    w.stocks_to_use                    as latest_wasde_stocks_to_use,
    w.season_avg_price                 as latest_wasde_season_avg_price
from {{ ref('stg_nass__price_marketing_year') }} p
left join latest_wasde w on w.my_start_year = p.my_start_year
