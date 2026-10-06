{#
    Historial de cambios (SCD tipo 2) de cada publicación de cada producto.

    Una versión nueva empieza cuando cambia alguno de los atributos seguidos (precio, precio
    original, envío gratis, tipo de logística o tipo de publicación), o cuando la publicación
    vuelve a aparecer después de haber faltado en un día en que el producto sí se consultó.

    valid_from: primer día de la versión.
    valid_to:   primer día en que ya no rige (cambió o la publicación desapareció). Exclusivo.
                Es null si la versión sigue vigente.

    Por qué un modelo y no `dbt snapshot`: bronze guarda la historia completa, así que el SCD2 se
    puede reconstruir entero desde ahí en cualquier momento. Un snapshot es estado acumulado: si
    se pierde o se corrompe, esa historia no se recupera. Este modelo es determinista y testeable.
#}

with prices as (

    select
        product_id,
        item_id,
        seller_id,
        snapshot_date,
        price,
        original_price,
        free_shipping,
        logistic_type,
        listing_type_id,
        -- coalesce explícito: concat_ws saltea los null y dos combinaciones distintas podrían dar igual
        md5(concat_ws(
            '|',
            price,
            coalesce(original_price::text, '∅'),
            coalesce(free_shipping::text, '∅'),
            coalesce(logistic_type, '∅'),
            coalesce(listing_type_id, '∅')
        )) as row_hash
    from {{ ref('stg_listing_prices') }}

),

product_days as (

    -- Días en que cada producto se consultó, numerados: sirven para detectar huecos
    select
        product_id,
        snapshot_date,
        row_number() over (partition by product_id order by snapshot_date) as day_n
    from (select distinct product_id, snapshot_date from prices) as days

),

flagged as (

    select
        prices.*,
        product_days.day_n,
        case
            when lag(prices.row_hash) over listing is distinct from prices.row_hash then 1
            when product_days.day_n - lag(product_days.day_n) over listing > 1 then 1  -- faltó al menos un día
            else 0
        end as is_new_version
    from prices
    join product_days using (product_id, snapshot_date)
    window listing as (partition by prices.product_id, prices.item_id order by prices.snapshot_date)

),

versioned as (

    select
        *,
        sum(is_new_version) over (
            partition by product_id, item_id
            order by snapshot_date
            rows between unbounded preceding and current row
        ) as version_n
    from flagged

),

bounds as (

    select
        product_id,
        item_id,
        version_n,
        min(snapshot_date)  as valid_from,
        max(snapshot_date)  as last_seen_date,
        max(day_n)          as last_day_n,
        count(*)            as days_observed
    from versioned
    group by product_id, item_id, version_n

)

select
    versioned.product_id || '|' || versioned.item_id || '|' || versioned.version_n as listing_version_id,
    versioned.product_id,
    versioned.item_id,
    versioned.seller_id,
    versioned.version_n,
    versioned.price,
    versioned.original_price,
    versioned.free_shipping,
    versioned.logistic_type,
    versioned.listing_type_id,
    bounds.valid_from,
    next_day.snapshot_date                  as valid_to,
    next_day.snapshot_date is null          as is_current,
    bounds.last_seen_date,
    bounds.days_observed
from versioned
join bounds
    on  bounds.product_id = versioned.product_id
    and bounds.item_id = versioned.item_id
    and bounds.version_n = versioned.version_n
left join product_days as next_day
    on  next_day.product_id = versioned.product_id
    and next_day.day_n = bounds.last_day_n + 1
where versioned.is_new_version = 1
