-- Cada publicación tiene como máximo una versión vigente.
select product_id, item_id, count(*) as versiones_vigentes
from {{ ref('scd_listing_prices') }}
where is_current
group by product_id, item_id
having count(*) > 1
