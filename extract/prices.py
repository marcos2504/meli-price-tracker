"""
Precios diarios: las publicaciones de cada producto seguido, vía /products/{id}/items.

Un 404 significa que hoy el producto no tiene vendedores. No es un error: se guarda
igual en bronze, así silver puede distinguir "sin datos" de "sin vendedores".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from extract.client import ApiResponse, MeliClient

log = logging.getLogger(__name__)


@dataclass
class PricesResult:
    responses: list[tuple[str, ApiResponse]]  # (product_id, respuesta)
    with_sellers: int
    without_sellers: int
    listings: int


def fetch_prices(
    client: MeliClient, product_ids: list[str], already_fetched: dict[str, ApiResponse] | None = None
) -> PricesResult:
    already_fetched = already_fetched or {}
    result = PricesResult(responses=[], with_sellers=0, without_sellers=0, listings=0)

    for pid in product_ids:
        resp = already_fetched.get(pid) or client.get(f"/products/{pid}/items")
        result.responses.append((pid, resp))
        if resp.ok:
            result.with_sellers += 1
            result.listings += len(resp.data.get("results", []))
        else:
            result.without_sellers += 1

    log.info(
        "Precios: %d productos con vendedores (%d publicaciones), %d sin vendedores",
        result.with_sellers,
        result.listings,
        result.without_sellers,
    )
    return result
