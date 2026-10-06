"""
Sink local: escribe la capa bronze como archivos JSON Lines, particionados por fecha.

    data/bronze/snapshot_date=2026-10-05/<run_id>.jsonl

Sirve para desarrollar y probar sin base de datos. En la Fase 2 se suma PostgresSink
con la misma interfaz, y el resto del extractor no cambia.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from extract.config import snapshot_date


class LocalJsonlSink:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir) / "bronze"

    def write_batch(self, records: list[dict[str, Any]], run_id: str) -> None:
        if not records:
            return
        day = snapshot_date().isoformat()
        folder = self.base_dir / f"snapshot_date={day}"
        folder.mkdir(parents=True, exist_ok=True)
        with (folder / f"{run_id}.jsonl").open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
