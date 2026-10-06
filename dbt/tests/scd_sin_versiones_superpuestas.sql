-- Dos versiones de la misma publicación no pueden estar vigentes al mismo tiempo.
select
    a.product_id,
    a.item_id,
    a.version_n as version_a,
    b.version_n as version_b
from {{ ref('scd_listing_prices') }} as a
join {{ ref('scd_listing_prices') }} as b
    on  a.product_id = b.product_id
    and a.item_id = b.item_id
    and a.version_n < b.version_n
where a.valid_to is null
   or a.valid_to > b.valid_from
