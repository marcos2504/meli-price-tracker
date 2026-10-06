"""
Carga en bronze un escenario de prueba chico pero con todos los casos que importan.
Lo usa el CI para correr dbt contra una base de prueba; NUNCA correrlo contra la base real.

Escenario (3 días, 2 productos):
    Producto P1 (iPhone)
        I1: 1000 → 950 el día 2 (cambio de precio). El día 3 hay dos corridas: la primera
            dice 900 y la segunda 950; tiene que ganar la última.
        I2: 1100 con precio original 1200 (descuento 8,33 %); desaparece el día 3.
        I3: aparece el día 2 a 1050.
    Producto P2 (Galaxy S24)
        I4: 2000 el día 1; el día 2 el producto no tiene vendedores (404); el día 3 vuelve a 2000.

Los resultados esperados los verifica scripts/check_ci.py.
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from extract import db  # noqa: E402
from extract.discovery import PostgresTrackedProducts, TrackedProduct  # noqa: E402
from extract.sinks import PostgresSink  # noqa: E402

DAY1 = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)  # 12:00 en Argentina


def listing(item_id, seller_id, price, original_price=None, state="Mendoza"):
    return {
        "item_id": item_id,
        "seller_id": seller_id,
        "price": price,
        "original_price": original_price,
        "currency_id": "ARS",
        "condition": "new",
        "listing_type_id": "gold_special",
        "official_store_id": None,
        "shipping": {"free_shipping": True, "logistic_type": "drop_off"},
        "seller_address": {"state": {"name": state}, "city": {"name": "Capital"}},
        "warranty": "Garantía de fábrica",
        "tags": [],
    }


def record(run_id, endpoint, path, product_id, status, fetched_at, payload):
    return {
        "run_id": run_id,
        "endpoint": endpoint,
        "path": path,
        "params": {},
        "status": status,
        "product_id": product_id,
        "fetched_at": fetched_at.isoformat(),
        "payload": payload,
    }


def items(run_id, product_id, day_offset, results, status=200, hour_offset=0):
    when = DAY1 + timedelta(days=day_offset, hours=hour_offset)
    payload = (
        {"paging": {"total": len(results)}, "results": results}
        if status == 200
        else {"message": "No winners"}
    )
    return record(
        run_id, "/products/{id}/items", f"/products/{product_id}/items", product_id, status, when, payload
    )


def detail(product_id, name, model):
    attributes = [
        {"id": "BRAND", "value_name": name.split()[0]},
        {"id": "MODEL", "value_name": model},
        {"id": "COLOR", "value_name": "Negro"},
        {"id": "INTERNAL_MEMORY", "value_name": "128 GB"},
    ]
    payload = {"id": product_id, "name": name, "domain_id": "MLA-CELLPHONES", "attributes": attributes}
    return record("run-1", "/products/{id}", f"/products/{product_id}", product_id, 200, DAY1, payload)


def build_records() -> list[dict]:
    i1, i2, i3, i4 = "MLAI1", "MLAI2", "MLAI3", "MLAI4"
    return [
        detail("MLAP1", "Apple iPhone 15 128 GB Negro", "iPhone 15"),
        detail("MLAP2", "Samsung Galaxy S24 128 GB Negro", "S24"),
        record("run-1", "/products/search", "/products/search", None, 200, DAY1, {"results": []}),
        # Día 1
        items("run-1", "MLAP1", 0, [listing(i1, 10, 1000), listing(i2, 11, 1100, original_price=1200)]),
        items("run-1", "MLAP2", 0, [listing(i4, 13, 2000, state="Córdoba")]),
        # Día 2
        items(
            "run-2", "MLAP1", 1, [listing(i1, 10, 950), listing(i2, 11, 1100, 1200), listing(i3, 12, 1050)]
        ),
        items("run-2", "MLAP2", 1, [], status=404),
        # Día 3: dos corridas; la segunda (una hora después) es la que vale
        items("run-3a", "MLAP1", 2, [listing(i1, 10, 900), listing(i3, 12, 1050)], hour_offset=0),
        items("run-3b", "MLAP1", 2, [listing(i1, 10, 950), listing(i3, 12, 1050)], hour_offset=1),
        items("run-3b", "MLAP2", 2, [listing(i4, 13, 2000, state="Córdoba")], hour_offset=1),
    ]


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url or "neon.tech" in url:
        sys.exit("seed_ci.py es solo para la base de prueba del CI")

    with db.connect(url) as conn:
        db.migrate(conn)
        conn.execute("truncate bronze.api_responses, ops.tracked_products")
        PostgresSink(conn).write_batch(build_records(), "seed")
        PostgresTrackedProducts(conn).save(
            [
                TrackedProduct("MLAP1", "Apple iPhone 15 128 GB Negro", "MLA-CELLPHONES", "iphone-15"),
                TrackedProduct(
                    "MLAP2", "Samsung Galaxy S24 128 GB Negro", "MLA-CELLPHONES", "samsung-galaxy-s24"
                ),
            ],
            "ci",
        )
    print("Escenario de prueba cargado en bronze")


if __name__ == "__main__":
    main()
