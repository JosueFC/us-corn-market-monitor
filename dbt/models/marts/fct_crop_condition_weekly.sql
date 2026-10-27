-- Weekly corn condition. Grain: state_key, week_ending. condition_index runs from 1 (all very poor)
-- to 5 (all excellent).
select
    state_key,
    week_ending                       as week_ending_key,
    week_number,
    pct_very_poor,
    pct_poor,
    pct_fair,
    pct_good,
    pct_excellent,
    (pct_very_poor * 1 + pct_poor * 2 + pct_fair * 3 + pct_good * 4 + pct_excellent * 5) / 100 as condition_index
from {{ ref('int_condition_weekly') }}
where n_classes = 5
