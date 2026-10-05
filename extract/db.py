"""
Conexión a PostgreSQL y migraciones.

Las migraciones son archivos SQL numerados en db/migrations/. Cada una se aplica una sola vez,
dentro de una transacción, y queda registrada en ops.schema_migrations.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from extract.config import ROOT

log = logging.getLogger(__name__)

MIGRATIONS_DIR = ROOT / "db" / "migrations"


def connect(database_url: str) -> psycopg.Connection:
    # autocommit: cada operación que necesita atomicidad abre su propio bloque conn.transaction()
    return psycopg.connect(
        database_url, autocommit=True, connect_timeout=15, application_name="meli-price-tracker"
    )


def migrate(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Aplica las migraciones pendientes y devuelve cuáles aplicó."""
    conn.execute("create schema if not exists ops")
    conn.execute(
        "create table if not exists ops.schema_migrations ("
        " version text primary key, applied_at timestamptz not null default now())"
    )
    applied = {row[0] for row in conn.execute("select version from ops.schema_migrations").fetchall()}

    new = []
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        if path.stem in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into ops.schema_migrations (version) values (%s)", (path.stem,))
        log.info("Migración aplicada: %s", path.stem)
        new.append(path.stem)
    return new
