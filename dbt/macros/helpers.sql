{# Quick Stats values arrive as text: "1,234", "4.29", or a suppression code such as "(D)". #}
{% macro nass_number(col) -%}
    try_cast(replace(trim({{ col }}), ',', '') as double)
{%- endmacro %}

{# The documented Quick Stats codes for a value that is not published. #}
{% macro nass_suppression(col) -%}
    case when trim({{ col }}) in ('(D)', '(NA)', '(Z)', '(S)', '(X)') then trim({{ col }}) end
{%- endmacro %}

{# 1-12 from a three-letter month abbreviation, NULL if it is not one. #}
{% macro month_number(col) -%}
    array_position(['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'], upper(trim({{ col }})))
{%- endmacro %}

{# The WASDE files mix 01/12/2021 and 2026-10-09 style dates. #}
{% macro parse_date(col) -%}
    cast(coalesce(
        try_strptime(trim({{ col }}), '%m/%d/%Y'),
        try_strptime(trim({{ col }}), '%Y-%m-%d'),
        try_strptime(trim({{ col }}), '%m/%d/%Y %H:%M:%S'),
        try_strptime(trim({{ col }}), '%Y-%m-%d %H:%M:%S'),
        try_strptime(trim({{ col }}), '%B %d, %Y')
    ) as date)
{%- endmacro %}

{# Corn marketing year (Sept-Aug) that a date falls in, identified by the calendar year it starts in. #}
{% macro marketing_year_start(date_col) -%}
    case when month({{ date_col }}) >= 9 then year({{ date_col }}) else year({{ date_col }}) - 1 end
{%- endmacro %}

{% macro marketing_year_label(start_year_col) -%}
    cast({{ start_year_col }} as varchar) || '/' || lpad(cast(({{ start_year_col }} + 1) % 100 as varchar), 2, '0')
{%- endmacro %}
