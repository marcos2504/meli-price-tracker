-- Cada precio diario de silver tiene que caer dentro de exactamente una versión del SCD2,
-- y con el mismo precio. Detecta errores en la lógica de cortes de versión.
with matches as (

    select
        p.listing_day_id,
        count(s.listing_version_id) as versiones,
        bool_and(s.price = p.price) as mismo_precio
    from {{ ref('stg_listing_prices') }} as p
    left join {{ ref('scd_listing_prices') }} as s
        on  s.product_id = p.product_id
        and s.item_id = p.item_id
        and p.snapshot_date >= s.valid_from
        and (s.valid_to is null or p.snapshot_date < s.valid_to)
    group by p.listing_day_id

)

select * from matches
where versiones <> 1 or not mismo_precio
