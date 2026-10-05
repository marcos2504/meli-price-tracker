"""Interfaz de destino de la capa bronze. El extractor solo conoce esta interfaz."""

from __future__ import annotations

from typing import Any, Protocol

from extract.client import ApiResponse


class Sink(Protocol):
    def write_batch(self, records: list[dict[str, Any]], run_id: str) -> None: ...


def to_record(resp: ApiResponse, run_id: str, endpoint: str, product_id: str | None = None) -> dict[str, Any]:
    """Convierte una respuesta de la API en una fila de bronze: el payload sin tocar más su metadata."""
    return {
        "run_id": run_id,
        "endpoint": endpoint,
        "path": resp.path,
        "params": resp.params,
        "status": resp.status,
        "product_id": product_id,
        "fetched_at": resp.fetched_at.isoformat(),
        "payload": resp.data,
    }
