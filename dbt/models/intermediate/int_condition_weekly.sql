-- Weekly condition ratings pivoted to one row per state and week, with a 1-5 condition index.
-- Grain: state_key, week_ending.
select
    state_key,
    any_value(state_alpha) as state_alpha,
    bool_or(is_national)   as is_national,
    week_ending,
    any_value(crop_year)   as crop_year,
    any_value(week_number) as week_number,
    max(case when rating_class = 'very poor' then pct_of_acres end) as pct_very_poor,
    max(case when rating_class = 'poor'      then pct_of_acres end) as pct_poor,
    max(case when rating_class = 'fair'      then pct_of_acres end) as pct_fair,
    max(case when rating_class = 'good'      then pct_of_acres end) as pct_good,
    max(case when rating_class = 'excellent' then pct_of_acres end) as pct_excellent,
    count(distinct rating_class)                                    as n_classes
from {{ ref('stg_nass__condition') }}
group by state_key, week_ending
