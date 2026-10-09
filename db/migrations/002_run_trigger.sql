-- Qué disparó cada corrida: 'schedule' (cron diario), 'workflow_dispatch' (manual desde GitHub)
-- o 'local'. Sirve para distinguir en el dashboard la corrida diaria de las pruebas y reintentos.
alter table ops.pipeline_runs add column if not exists triggered_by text;
