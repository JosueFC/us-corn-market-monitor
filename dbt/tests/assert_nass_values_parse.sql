-- A Quick Stats value is either a number or one of the documented suppression codes. Anything else is a
-- format change that would otherwise turn into a silent null.
{% set models = ['stg_nass__price_monthly', 'stg_nass__price_marketing_year', 'stg_nass__annual',
                 'stg_nass__stocks', 'stg_nass__condition'] %}
{% for m in models %}
select '{{ m }}' as model, value_as_published
from {{ ref(m) }}
where value_as_published is not null and value_as_published <> ''
  and {{ nass_number('value_as_published') }} is null and suppression_code is null
{{ "union all" if not loop.last }}
{% endfor %}
