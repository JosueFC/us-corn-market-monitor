-- A new or renamed WASDE attribute must be added to seeds/wasde_attribute_map.csv (as a mapped or an
-- ignored item) before it is silently dropped from the balance sheet.
select distinct s.attribute_label, s.attribute_key
from {{ ref('stg_wasde__corn_us') }} s
left join {{ ref('wasde_attribute_map') }} m using (attribute_key)
where m.attribute_key is null
