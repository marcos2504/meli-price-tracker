{#
    Nombre de producto para mostrar. Los nombres del catálogo de MercadoLibre vienen como los
    cargó cada vendedor: "Galaxy S24 , Negro Onyx, 8gb_256gb" o "Apple Iphone 15 128 Gb".
    Se corrige lo más común sin tocar el nombre original, que queda en la columna name.
    Cada regexp_replace es un paso, de adentro hacia afuera:
      1. guiones bajos → espacios          4. iphone → iPhone, playstation → PlayStation
      2. espacios antes de una coma        5. 5g → 5G
      3. 8gb / 8 Gb → 8 GB, 2tb → 2 TB      6. espacios repetidos
    (SQL escrito a mano y no generado con Jinja: Jinja interpretaría las barras invertidas.)
#}
{% macro clean_product_name(column) %}
    trim(regexp_replace(
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        regexp_replace(
                            regexp_replace(
                                replace({{ column }}, '_', ' '),
                                '\s+,', ',', 'g'),
                            '(\d+)\s*gb\M', '\1 GB', 'gi'),
                        '(\d+)\s*tb\M', '\1 TB', 'gi'),
                    '\miphone\M', 'iPhone', 'gi'),
                '\mplaystation\M', 'PlayStation', 'gi'),
            '(\d)g\M', '\1G', 'gi'),
        '\s{2,}', ' ', 'g'))
{% endmacro %}
