{# Fails for non-null values outside [min_value, max_value]; catches unit mix-ups such as cents versus dollars. #}
{% test between_values(model, column_name, min_value, max_value) %}
select {{ column_name }}
from {{ model }}
where {{ column_name }} is not null
  and ({{ column_name }} < {{ min_value }} or {{ column_name }} > {{ max_value }})
{% endtest %}
