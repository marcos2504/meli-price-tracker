"""
Sink de Postgres: escribe la capa bronze en bronze.api_responses.

Cada lote se inserta en una sola transacción: o entra completo o no entra nada.
Los payloads van como JSON serializado con cast a jsonb, así el módulo no depende de psycopg
y se puede testear con una conexión simulada.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from extract.config import snapshot_date

INSERT_SQL = """
    insert into bronze.api_responses
        (run_id, snapshot_date, endpoint, path, params, status, product_id, fetched_at, payload)
    values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s::jsonb)
"""


def _json(value: Any) -> str:
    # Postgres no acepta el carácter \u0000 dentro de jsonb: se descarta si llegara a aparecer
    return json.dumps(value, ensure_ascii=False).replace("\\u0000", "")


class PostgresSink:
    def __init__(self, conn):
        self.conn = conn

    def write_batch(self, records: list[dict[str, Any]], run_id: str) -> None:
        if not records:
            return
        rows = [
            (
                r["run_id"],
                snapshot_date(datetime.fromisoformat(r["fetched_at"])),
                r["endpoint"],
                r["path"],
                _json(r["params"]),
                r["status"],
                r["product_id"],
                r["fetched_at"],
                _json(r["payload"]),
            )
            for r in records
        ]
        with self.conn.transaction(), self.conn.cursor() as cur:
            cur.executemany(INSERT_SQL, rows)
