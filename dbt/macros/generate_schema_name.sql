{#
    Por defecto dbt antepone el schema del perfil al configurado (quedaría "silver_gold").
    Acá cada carpeta escribe exactamente en el schema que declara: silver o gold.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
