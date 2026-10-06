-- Hechos por publicación y día: precio, descuento y condiciones de venta.

select
    listing_day_id,
    snapshot_date,
    product_id,
    item_id,
    seller_id,
    price,
    original_price,
    case
        when original_price > price
            then round(100.0 * (original_price - price) / original_price, 2)
    end as discount_pct,
    currency_id,
    condition,
    listing_type_id,
    free_shipping,
    logistic_type,
    logistic_type = 'fulfillment' as is_full
from {{ ref('stg_listing_prices') }}
