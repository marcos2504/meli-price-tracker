{#
    Mayores bajas y subas del precio mínimo de cada producto en la última semana.

    Compara el precio mínimo del último día disponible contra el del día más reciente que tenga
    al menos 7 días de antigüedad. Si todavía no hay una semana de historia, compara contra el
    primer día disponible y lo indica en days_compared.
#}

with daily as (

    select product_id, snapshot_date, min_price, sellers
    from {{ ref('fct_product_daily') }}

),

as_of as (

    select max(snapshot_date) as as_of_date from daily

),

current_prices as (

    select daily.*
    from daily
    cross join as_of
    where daily.snapshot_date = as_of.as_of_date

),

week_ago as (

    select distinct on (daily.product_id)
        daily.product_id,
        daily.snapshot_date as base_date,
        daily.min_price     as base_price
    from daily
    cross join as_of
    where daily.snapshot_date <= as_of.as_of_date - 7
    order by daily.product_id, daily.snapshot_date desc

),

first_seen as (

    select distinct on (product_id)
        product_id,
        snapshot_date as base_date,
        min_price     as base_price
    from daily
    order by product_id, snapshot_date

),

compared as (

    select
        c.product_id,
        coalesce(w.base_date, f.base_date)      as base_date,
        c.snapshot_date                         as as_of_date,
        coalesce(w.base_price, f.base_price)    as base_price,
        c.min_price                             as current_price,
        c.sellers
    from current_prices as c
    left join week_ago as w on w.product_id = c.product_id
    left join first_seen as f on f.product_id = c.product_id

)

select
    compared.product_id,
    p.name,
    p.search,
    compared.base_date,
    compared.as_of_date,
    compared.as_of_date - compared.base_date                                    as days_compared,
    compared.base_price,
    compared.current_price,
    compared.current_price - compared.base_price                                as change_amount,
    round(100.0 * (compared.current_price - compared.base_price) / compared.base_price, 2) as change_pct,
    compared.sellers,
    rank() over (order by (compared.current_price - compared.base_price) / compared.base_price) as drop_rank
from compared
left join {{ ref('dim_product') }} as p
    on p.product_id = compared.product_id
