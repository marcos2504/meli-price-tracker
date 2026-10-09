-- Dimensión de productos: atributos del catálogo más la búsqueda de la watchlist que lo trajo.

select
    p.product_id,
    p.name,
    {{ clean_product_name('p.name') }} as display_name,
    p.brand,
    p.line,
    p.model,
    p.color,
    p.internal_memory,
    p.ram,
    p.domain_id,
    t.search,
    t.product_id is not null as is_tracked
from {{ ref('stg_products') }} as p
left join {{ source('ops', 'tracked_products') }} as t
    on t.product_id = p.product_id
