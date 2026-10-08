"""
Verifica que dbt calculó lo esperado para el escenario de scripts/seed_ci.py.

Los tests de dbt validan reglas generales (unicidad, nulos, relaciones). Este script valida
resultados concretos: si alguien cambia la lógica del SCD2 o de las métricas y da otros números,
el CI falla.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal

import psycopg

D1, D2, D3 = date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)

failures: list[str] = []


def check(name: str, actual, expected) -> None:
    status = "OK  " if actual == expected else "FAIL"
    print(f"[{status}] {name}: {actual!r}" + ("" if actual == expected else f" (esperado {expected!r})"))
    if actual != expected:
        failures.append(name)


def main() -> None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731

        check("filas en stg_listing_prices", q("select count(*) from silver.stg_listing_prices")[0][0], 9)

        check(
            "el día 3 gana la última corrida (I1 = 950)",
            q(
                "select price from silver.stg_listing_prices"
                " where item_id = 'MLAI1' and snapshot_date = '2026-01-07'"
            ),
            [(Decimal("950.00"),)],
        )

        scd = {
            (r[0], r[1]): r[2:]
            for r in q(
                "select item_id, version_n, price, valid_from, valid_to, is_current "
                "from silver.scd_listing_prices"
            )
        }
        check("SCD2: cantidad de versiones", len(scd), 5)
        check("SCD2: I1 v1", scd.get(("MLAI1", 1)), (Decimal("1000.00"), D1, D2, False))
        check("SCD2: I1 v2 vigente", scd.get(("MLAI1", 2)), (Decimal("950.00"), D2, None, True))
        check(
            "SCD2: I2 se cierra cuando desaparece", scd.get(("MLAI2", 1)), (Decimal("1100.00"), D1, D3, False)
        )
        check("SCD2: I3 aparece el día 2", scd.get(("MLAI3", 1)), (Decimal("1050.00"), D2, None, True))
        check("SCD2: I4 una sola versión", scd.get(("MLAI4", 1)), (Decimal("2000.00"), D1, None, True))

        check(
            "descuento de I2",
            q(
                "select discount_pct from gold.fct_listing_daily"
                " where item_id = 'MLAI2' and snapshot_date = '2026-01-05'"
            ),
            [(Decimal("8.33"),)],
        )

        check(
            "fct_product_daily P1 día 2 (vendedores, mínimo, variación)",
            q(
                "select sellers, min_price, min_price_change_pct from gold.fct_product_daily "
                "where product_id = 'MLAP1' and snapshot_date = '2026-01-06'"
            ),
            [(3, Decimal("950.00"), Decimal("-5.00"))],
        )

        check(
            "mart_weekly_movers P1",
            q(
                "select change_pct, days_compared, drop_rank from gold.mart_weekly_movers "
                "where product_id = 'MLAP1'"
            ),
            [(Decimal("-5.00"), 2, 1)],
        )

        check(
            "display_name limpia el nombre del catálogo",
            q("select display_name from gold.dim_product where product_id = 'MLAP2'"),
            [("Samsung Galaxy S24, Negro Onyx, 8 GB 256 GB",)],
        )

        check(
            "dim_product toma los atributos del catálogo",
            q("select brand, model, search from gold.dim_product where product_id = 'MLAP2'"),
            [("Samsung", "S24", "samsung-galaxy-s24")],
        )

        # Solo I1 cambió de precio; la desaparición de I2 y la aparición de I3 no son cambios
        check(
            "fct_price_changes (fecha, publicación, anterior, nuevo, %)",
            q(
                "select change_date, item_id, previous_price, new_price, change_pct"
                " from gold.fct_price_changes"
            ),
            [(D2, "MLAI1", Decimal("1000.00"), Decimal("950.00"), Decimal("-5.00"))],
        )

    if failures:
        sys.exit(f"\n{len(failures)} verificación(es) fallaron: {', '.join(failures)}")
    print("\nTodas las verificaciones pasaron")


if __name__ == "__main__":
    main()
