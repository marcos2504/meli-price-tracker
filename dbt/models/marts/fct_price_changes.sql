{#
    Hechos de cambio de precio: una fila por cada vez que una publicación cambió de precio.
    Es otro grano que fct_listing_daily (un evento, no un día), por eso es una tabla de hechos aparte.

    Sale del SCD2: una versión nueva con precio distinto al de la versión anterior es un cambio.
    Una versión nueva también puede deberse a cambios de envío o a que la publicación reapareció;
    esas se excluyen si el precio no cambió. Los atributos del producto se toman de dim_product.
#}

with versions as (

    select
        product_id,
        item_id,
        seller_id,
        version_n,
        valid_from,
        price,
        lag(price) over (partition by product_id, item_id order by version_n) as previous_price
    from {{ ref('scd_listing_prices') }}

)

select
    product_id || '|' || item_id || '|' || version_n                as price_change_id,
    valid_from                                                      as change_date,
    product_id,
    item_id,
    seller_id,
    previous_price,
    price                                                           as new_price,
    price - previous_price                                          as change_amount,
    round(100.0 * (price - previous_price) / previous_price, 2)     as change_pct
from versions
where previous_price is not null
  and price <> previous_price
