{#
    Una fila por producto de catálogo, con sus atributos principales.
    Se toma la respuesta más reciente de /products/{id} de cada producto.
#}

with responses as (

    select
        product_id,
        fetched_at,
        payload,
        row_number() over (partition by product_id order by fetched_at desc) as response_rank
    from {{ source('bronze', 'api_responses') }}
    where endpoint = '/products/{id}'
      and status = 200

)

select
    product_id,
    payload ->> 'name'                                  as name,
    payload ->> 'domain_id'                             as domain_id,
    payload ->> 'family_name'                           as family_name,
    payload ->> 'status'                                as catalog_status,
    {{ catalog_attribute('payload', 'BRAND') }}         as brand,
    {{ catalog_attribute('payload', 'LINE') }}          as line,
    {{ catalog_attribute('payload', 'MODEL') }}         as model,
    {{ catalog_attribute('payload', 'COLOR') }}         as color,
    {{ catalog_attribute('payload', 'INTERNAL_MEMORY') }} as internal_memory,
    {{ catalog_attribute('payload', 'RAM') }}           as ram,
    fetched_at                                          as detail_fetched_at
from responses
where response_rank = 1
