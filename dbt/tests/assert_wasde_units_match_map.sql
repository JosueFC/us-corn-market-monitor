-- Units must be the ones the map expects (million bushels, million acres, bushels, $/bu).
select s.wasde_number, s.attribute_label, s.unit, m.expected_unit
from {{ ref('stg_wasde__corn_us') }} s
join {{ ref('wasde_attribute_map') }} m using (attribute_key)
where s.unit <> m.expected_unit
