{#
    Devuelve el valor de un atributo del catálogo (BRAND, MODEL, COLOR...) a partir del array
    `attributes` de la respuesta de /products/{id}.
#}
{% macro catalog_attribute(payload, attribute_id) -%}
    (
        select a ->> 'value_name'
        from jsonb_array_elements({{ payload }} -> 'attributes') as a
        where a ->> 'id' = '{{ attribute_id }}'
        limit 1
    )
{%- endmacro %}
