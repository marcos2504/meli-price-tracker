{# Test genérico: falla con las filas donde la columna es cero o negativa. #}
{% test positive_value(model, column_name) %}
    select *
    from {{ model }}
    where {{ column_name }} <= 0
{% endtest %}
