-- Usuario de solo lectura para el dashboard de Streamlit.
-- Requiere las migraciones aplicadas (python -m extract db-init): usa ops.pipeline_runs.triggered_by.
--
-- Correr UNA vez en el SQL Editor de Neon, conectado con el mismo usuario que usa el pipeline
-- (neondb_owner): los default privileges de abajo aplican a las tablas que cree ese usuario.
-- No es una migración porque lleva una contraseña: reemplazar CAMBIAR_POR_UNA_CLAVE_LARGA antes de
-- ejecutarlo, y no commitear el archivo con la contraseña real. El CI corre este mismo archivo
-- contra su base de prueba para verificar que los permisos alcanzan (y que no sobran).

create role dashboard_reader with login password 'CAMBIAR_POR_UNA_CLAVE_LARGA';

-- La app es pública: aunque alguien consiguiera la contraseña, no puede escribir ni trabar la base
alter role dashboard_reader set default_transaction_read_only = on;
alter role dashboard_reader set statement_timeout = '15s';

-- Puede leer gold (todo lo que consume el dashboard)
grant usage on schema gold to dashboard_reader;
grant select on all tables in schema gold to dashboard_reader;

-- dbt recrea las tablas de gold en cada corrida: sin esto, el permiso se perdería al día siguiente.
-- Cada tabla nueva que cree el usuario actual en gold queda legible para dashboard_reader.
alter default privileges in schema gold grant select on tables to dashboard_reader;

-- Del registro de corridas, solo las columnas que muestra la página de salud.
-- error y http quedan afuera: pueden tener detalles internos que no tienen por qué ser públicos.
grant usage on schema ops to dashboard_reader;
grant select (
    run_id, started_at, finished_at, duration_s, status, triggered_by, discovery,
    products, products_with_sellers, products_without_sellers, listings
) on ops.pipeline_runs to dashboard_reader;

-- Verificación: tiene que listar las tablas de gold (y ops.pipeline_runs aparece en la de columnas)
select table_schema, table_name
from information_schema.role_table_grants
where grantee = 'dashboard_reader'
order by 1, 2;
