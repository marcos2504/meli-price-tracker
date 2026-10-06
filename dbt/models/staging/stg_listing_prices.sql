{#
    Una fila por publicación, producto y día.

    Si el pipeline corrió más de una vez en el día, se usa la última respuesta de cada producto.
    Incremental: cada corrida reprocesa los últimos días (por si hubo corridas tardías) y
    reemplaza esos productos-día completos, así una publicación que desapareció en una
    corrida posterior no queda colgada.
#}
{{
    config(
        materialized='incremental',
        incremental_strategy='delete+insert',
        unique_key=['snapshot_date', 'product_id'],
    )
}}

{% set lookback_days = 3 %}

with responses as (

    select
        run_id,
        snapshot_date,
        product_id,
        status,
        fetched_at,
        payload,
        row_number() over (
            partition by snapshot_date, product_id
            order by fetched_at desc
        ) as response_rank
    from {{ source('bronze', 'api_responses') }}
    where endpoint = '/products/{id}/items'
    {% if is_incremental() %}
        and snapshot_date >= (
            select coalesce(max(snapshot_date), date '1900-01-01') - {{ lookback_days }}
            from {{ this }}
        )
    {% endif %}

),

latest as (

    -- La última respuesta del día manda, aunque haya sido un 404 (producto sin vendedores)
    select * from responses
    where response_rank = 1
      and status = 200

),

listings as (

    select
        latest.snapshot_date,
        latest.product_id,
        latest.run_id,
        latest.fetched_at,
        item.value as item
    from latest
    cross join lateral jsonb_array_elements(latest.payload -> 'results') as item(value)

)

select
    snapshot_date || '|' || product_id || '|' || (item ->> 'item_id')   as listing_day_id,
    snapshot_date,
    product_id,
    item ->> 'item_id'                                                  as item_id,
    (item ->> 'seller_id')::bigint                                      as seller_id,
    (item ->> 'price')::numeric(14, 2)                                  as price,
    (item ->> 'original_price')::numeric(14, 2)                         as original_price,
    item ->> 'currency_id'                                              as currency_id,
    item ->> 'condition'                                                as condition,
    item ->> 'listing_type_id'                                          as listing_type_id,
    (item -> 'shipping' ->> 'free_shipping')::boolean                   as free_shipping,
    item -> 'shipping' ->> 'logistic_type'                              as logistic_type,
    item ->> 'official_store_id'                                        as official_store_id,
    item -> 'seller_address' -> 'state' ->> 'name'                      as seller_state,
    item -> 'seller_address' -> 'city' ->> 'name'                       as seller_city,
    item ->> 'warranty'                                                 as warranty,
    item -> 'tags'                                                      as tags,
    run_id,
    fetched_at
from listings
