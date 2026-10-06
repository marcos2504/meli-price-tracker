{#
    Hechos por producto y día: cómo se distribuyen los precios entre los vendedores que compiten.

    price_gap_pct: cuánto más caro es el segundo vendedor más barato respecto del más barato.
    Una brecha grande indica que el precio mínimo es una oportunidad puntual.
#}

with daily as (

    select
        snapshot_date,
        product_id,
        count(*)                                                    as sellers,
        min(price)                                                  as min_price,
        percentile_cont(0.5) within group (order by price)::numeric(14, 2) as median_price,
        max(price)                                                  as max_price,
        (array_agg(price order by price))[2]                        as second_min_price,
        min(price) filter (where condition = 'new')                 as min_price_new,
        count(*) filter (where free_shipping)                       as sellers_free_shipping
    from {{ ref('stg_listing_prices') }}
    group by snapshot_date, product_id

)

select
    snapshot_date || '|' || product_id as product_day_id,
    snapshot_date,
    product_id,
    sellers,
    min_price,
    median_price,
    max_price,
    second_min_price,
    case
        when second_min_price is not null
            then round(100.0 * (second_min_price - min_price) / min_price, 2)
    end as price_gap_pct,
    min_price_new,
    sellers_free_shipping,
    lag(min_price) over (partition by product_id order by snapshot_date) as prev_min_price,
    round(
        100.0 * (min_price - lag(min_price) over (partition by product_id order by snapshot_date))
        / nullif(lag(min_price) over (partition by product_id order by snapshot_date), 0),
        2
    ) as min_price_change_pct
from daily
