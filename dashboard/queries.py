"""
Consultas del dashboard. Leen el star schema de gold directamente: los joins entre hechos y
dimensiones se hacen acá, no en tablas extra dentro de dbt.

Están separadas de las páginas para que el CI pueda ejecutarlas contra el Postgres de prueba
(scripts/check_dashboard.py) sin levantar Streamlit.
"""

# Resumen del último día con datos
OVERVIEW = """
with last_day as (
    select max(snapshot_date) as as_of_date from gold.fct_listing_daily
)
select
    l.as_of_date,
    (select count(distinct snapshot_date) from gold.fct_product_daily) as days_of_history,
    count(distinct f.product_id)                                       as products,
    count(*)                                                           as listings,
    count(distinct f.seller_id)                                        as sellers
from gold.fct_listing_daily as f
join last_day as l
    on f.snapshot_date = l.as_of_date
group by l.as_of_date
"""

# Variación del precio más barato de cada producto (no de cada publicación)
MOVERS = """
select
    product_id,
    display_name,
    search,
    base_date,
    as_of_date,
    days_compared,
    base_price,
    current_price,
    change_amount,
    change_pct,
    sellers
from gold.mart_weekly_movers
order by change_pct, display_name
"""

# Productos con historia de precios, para el selector
PRODUCTS = """
select p.product_id, p.display_name, coalesce(p.search, 'otros') as search
from gold.dim_product as p
where exists (
    select 1 from gold.fct_product_daily as f where f.product_id = p.product_id
)
order by search, p.display_name
"""

PRODUCT_HISTORY = """
select
    snapshot_date,
    sellers,
    min_price,
    median_price,
    max_price,
    price_gap_pct,
    min_price_change_pct
from gold.fct_product_daily
where product_id = %(product_id)s
order by snapshot_date
"""

# Publicaciones del producto en su último día con datos, de la más barata a la más cara
PRODUCT_LISTINGS = """
select
    f.price,
    f.original_price,
    f.discount_pct,
    f.condition,
    f.free_shipping,
    f.is_full,
    s.is_official_store,
    s.seller_city,
    s.seller_state,
    f.item_id
from gold.fct_listing_daily as f
left join gold.dim_seller as s
    on s.seller_id = f.seller_id
where f.product_id = %(product_id)s
  and f.snapshot_date = (
      select max(snapshot_date) from gold.fct_listing_daily where product_id = %(product_id)s
  )
order by f.price
"""

# Cambios de precio de cada publicación en los últimos N días, contados desde el último día con
# datos (no desde hoy: si el pipeline se frena unos días, la página sigue mostrando algo)
PRICE_CHANGES = """
select
    c.change_date,
    p.display_name  as product_name,
    p.search,
    c.item_id,
    c.previous_price,
    c.new_price,
    c.change_amount,
    c.change_pct
from gold.fct_price_changes as c
join gold.dim_product as p
    on p.product_id = c.product_id
where c.change_date >= (select max(snapshot_date) from gold.fct_product_daily) - %(days)s::int
order by c.change_date desc, c.change_pct
"""

# Sin las columnas error ni http: el usuario del dashboard no tiene permiso sobre ellas
PIPELINE_RUNS = """
select
    run_id,
    started_at,
    duration_s,
    status,
    triggered_by,
    discovery,
    products,
    listings
from ops.pipeline_runs
order by started_at desc
limit %(limit)s
"""
