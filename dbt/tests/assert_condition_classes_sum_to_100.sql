-- The five condition classes of a week should add up to 100 percent (published values are rounded).
select state_key, week_ending, pct_very_poor + pct_poor + pct_fair + pct_good + pct_excellent as total_pct
from {{ ref('int_condition_weekly') }}
where n_classes = 5
  and abs(pct_very_poor + pct_poor + pct_fair + pct_good + pct_excellent - 100) > 3
