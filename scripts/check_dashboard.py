"""
Verifica el dashboard contra el escenario de scripts/seed_ci.py, conectado como dashboard_reader.

1. Cada consulta de dashboard/queries.py corre con el usuario de solo lectura y devuelve lo esperado.
2. El usuario no puede ver columnas sensibles ni escribir.
3. Cada página de Streamlit se ejecuta sin excepciones (streamlit.testing.AppTest).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"
sys.path.insert(0, str(DASHBOARD))

import queries as q  # noqa: E402

PAGES = ["views/producto.py", "views/cambios.py", "views/salud.py"]

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def check_queries(url: str) -> None:
    # Cantidad de filas esperada para el escenario de prueba (None = solo tiene que correr)
    cases = [
        ("OVERVIEW", q.OVERVIEW, {}, 1),
        ("MOVERS", q.MOVERS, {}, 2),
        ("PRODUCTS", q.PRODUCTS, {}, 2),
        ("PRODUCT_HISTORY", q.PRODUCT_HISTORY, {"product_id": "MLAP1"}, 3),
        ("PRODUCT_LISTINGS", q.PRODUCT_LISTINGS, {"product_id": "MLAP1"}, 2),
        ("PRICE_CHANGES", q.PRICE_CHANGES, {"days": 30}, 1),
        ("PIPELINE_RUNS", q.PIPELINE_RUNS, {"limit": 60}, 2),
    ]
    with psycopg.connect(url) as conn:
        for name, sql, params, expected in cases:
            rows = conn.execute(sql, params or None).fetchall()
            check(f"consulta {name}", expected is None or len(rows) == expected, f"{len(rows)} filas")


def check_permissions(url: str) -> None:
    forbidden = [
        ("no ve ops.pipeline_runs.error", "select error from ops.pipeline_runs"),
        ("no ve los tokens de OAuth", "select * from ops.auth_tokens"),
        ("no lee bronze", "select count(*) from bronze.api_responses"),
        ("no puede escribir", "delete from gold.dim_product"),
    ]
    for name, sql in forbidden:
        with psycopg.connect(url) as conn:
            try:
                conn.execute(sql)
                check(name, False, "la consulta funcionó y no debería")
            except psycopg.errors.InsufficientPrivilege:
                check(name, True)
            except psycopg.errors.ReadOnlySqlTransaction:
                check(name, True, "bloqueado por default_transaction_read_only")


def check_pages() -> None:
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=60)
    at.run()
    check_page(at, "views/resumen.py (inicio)")
    for page in PAGES:
        at.switch_page(page)
        at.run()
        check_page(at, page)


def check_page(at, name: str) -> None:
    problems = [e.value for e in at.exception] + [e.value for e in at.error]
    check(f"página {name}", not problems, "; ".join(map(str, problems)))


def main() -> None:
    url = os.environ["DASHBOARD_DATABASE_URL"]
    check_queries(url)
    check_permissions(url)
    check_pages()

    if failures:
        sys.exit(f"\n{len(failures)} verificación(es) fallaron: {', '.join(failures)}")
    print("\nEl dashboard pasó todas las verificaciones")


if __name__ == "__main__":
    main()
