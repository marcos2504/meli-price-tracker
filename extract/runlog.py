"""
Registro de corridas del pipeline.

- `FileRunLog`: un JSON por corrida en data/runs/ (modo local).
- `PostgresRunLog`: una fila por corrida en ops.pipeline_runs. Se inserta al empezar con
  status 'running' y se actualiza al terminar: si el proceso muere a la mitad, la corrida
  queda visible como 'running' y no desaparece sin rastro.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol


class RunLog(Protocol):
    def start(self, summary: dict) -> None: ...
    def finish(self, summary: dict) -> None: ...


class FileRunLog:
    def __init__(self, data_dir: Path):
        self.folder = Path(data_dir) / "runs"

    def start(self, summary: dict) -> None:
        pass  # el archivo se escribe al final, con el resumen completo

    def finish(self, summary: dict) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / f"{summary['run_id']}.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )


class PostgresRunLog:
    def __init__(self, conn):
        self.conn = conn

    def start(self, summary: dict) -> None:
        self.conn.execute(
            "insert into ops.pipeline_runs (run_id, started_at, status) values (%s, %s, 'running')",
            (summary["run_id"], summary["started_at"]),
        )

    def finish(self, summary: dict) -> None:
        self.conn.execute(
            """
            update ops.pipeline_runs set
                finished_at = %s,
                duration_s = %s,
                status = %s,
                discovery = %s,
                products = %s,
                products_with_sellers = %s,
                products_without_sellers = %s,
                listings = %s,
                http = %s::jsonb,
                error = %s
            where run_id = %s
            """,
            (
                summary.get("finished_at"),
                summary.get("duration_s"),
                summary["status"],
                summary.get("discovery"),
                summary.get("products"),
                summary.get("products_with_sellers"),
                summary.get("products_without_sellers"),
                summary.get("listings"),
                json.dumps(summary.get("http")),
                summary.get("error"),
                summary["run_id"],
            ),
        )
