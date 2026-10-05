-- Migración inicial: capas medallion y estado operativo del pipeline.
-- silver y gold quedan vacíos: los llena dbt en la Fase 3.

create schema if not exists bronze;
create schema if not exists silver;
create schema if not exists gold;
create schema if not exists ops;

-- Bronze: cada respuesta de la API tal cual llegó. Solo se agregan filas, nunca se modifican.
-- La deduplicación (por ejemplo, dos corridas el mismo día) la resuelve silver.
create table if not exists bronze.api_responses (
    id              bigserial primary key,
    run_id          text        not null,
    snapshot_date   date        not null,   -- fecha de la corrida en hora de Argentina
    endpoint        text        not null,   -- plantilla del endpoint, ej. /products/{id}/items
    path            text        not null,   -- path concreto consultado
    params          jsonb       not null default '{}'::jsonb,
    status          smallint    not null,
    product_id      text,
    fetched_at      timestamptz not null,
    payload         jsonb,
    loaded_at       timestamptz not null default now()
);

create index if not exists api_responses_snapshot_idx on bronze.api_responses (snapshot_date, endpoint);
create index if not exists api_responses_product_idx  on bronze.api_responses (product_id, fetched_at);
create index if not exists api_responses_run_idx      on bronze.api_responses (run_id);

-- Tokens OAuth vigentes. Una sola fila: MercadoLibre rota el refresh token en cada uso.
create table if not exists ops.auth_tokens (
    id              smallint    primary key default 1 check (id = 1),
    access_token    text        not null,
    refresh_token   text        not null,
    expires_at      timestamptz not null,
    user_id         bigint,
    updated_at      timestamptz not null default now()
);

-- Productos que el pipeline sigue a diario, resultado del último descubrimiento.
create table if not exists ops.tracked_products (
    product_id              text        primary key,
    name                    text        not null,
    domain_id               text,
    search                  text        not null,   -- nombre de la búsqueda de watchlist.toml
    refreshed_at            timestamptz not null,
    watchlist_fingerprint   text        not null
);

-- Registro de cada corrida. Se inserta al empezar (status = running) y se actualiza al terminar,
-- así una corrida que se cae a la mitad también queda visible.
create table if not exists ops.pipeline_runs (
    run_id                      text        primary key,
    started_at                  timestamptz not null,
    finished_at                 timestamptz,
    duration_s                  numeric(10, 1),
    status                      text        not null check (status in ('running', 'success', 'failed')),
    discovery                   boolean,
    products                    integer,
    products_with_sellers       integer,
    products_without_sellers    integer,
    listings                    integer,
    http                        jsonb,
    error                       text
);
