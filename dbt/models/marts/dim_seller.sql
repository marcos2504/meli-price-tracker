-- Dimensión de vendedores, con la ubicación más reciente informada.

with ranked as (

    select
        seller_id,
        seller_state,
        seller_city,
        official_store_id,
        snapshot_date,
        row_number() over (partition by seller_id order by snapshot_date desc, fetched_at desc) as recency
    from {{ ref('stg_listing_prices') }}

),

activity as (

    select
        seller_id,
        min(snapshot_date)                  as first_seen_date,
        max(snapshot_date)                  as last_seen_date,
        count(distinct product_id)          as products_offered,
        bool_or(official_store_id is not null) as is_official_store
    from {{ ref('stg_listing_prices') }}
    group by seller_id

)

select
    activity.seller_id,
    ranked.seller_state,
    ranked.seller_city,
    activity.is_official_store,
    activity.products_offered,
    activity.first_seen_date,
    activity.last_seen_date
from activity
join ranked
    on ranked.seller_id = activity.seller_id
   and ranked.recency = 1
