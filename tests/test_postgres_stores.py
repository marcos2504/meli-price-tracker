"""
Tests unitarios de los stores de Postgres con una conexión simulada.

Verifican qué SQL se ejecuta, con qué parámetros y dentro de qué transacción. El SQL real contra
una base se prueba en tests/test_postgres_integration.py (requiere TEST_DATABASE_URL).
"""

import json
import re
import tempfile
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from extract.auth import PostgresTokenStore, TokenManager, Tokens
from extract.discovery import PostgresTrackedProducts, TrackedProduct
from extract.runlog import PostgresRunLog
from extract.sinks import PostgresSink
from tests.fakes import FakeResponse, FakeSession, oauth_body, settings, valid_tokens


class FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    """Registra cada sentencia y si se ejecutó dentro de una transacción."""

    def __init__(self, results: dict[str, list] | None = None):
        self.results = results or {}  # regex sobre el SQL -> filas a devolver
        self.statements: list[tuple[str, object, int]] = []  # (sql, params, nivel de transacción)
        self.depth = 0

    def execute(self, sql, params=None):
        self.statements.append((" ".join(sql.split()), params, self.depth))
        for pattern, rows in self.results.items():
            if re.search(pattern, sql, re.S):
                return FakeResult(rows)
        return FakeResult([])

    @contextmanager
    def transaction(self):
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    @contextmanager
    def cursor(self):
        conn = self

        class Cursor:
            def executemany(self, sql, rows):
                for row in rows:
                    conn.statements.append((" ".join(sql.split()), row, conn.depth))

        yield Cursor()

    def sql(self, pattern: str) -> list[tuple[str, object, int]]:
        return [s for s in self.statements if re.search(pattern, s[0], re.I)]


class PostgresSinkTests(unittest.TestCase):
    def test_inserta_el_lote_en_una_transaccion_con_fecha_argentina(self):
        conn = FakeConn()
        records = [
            {
                "run_id": "r1",
                "endpoint": "/products/{id}/items",
                "path": "/products/MLA1/items",
                "params": {},
                "status": 200,
                "product_id": "MLA1",
                # 01:30 UTC del 6 de octubre = 22:30 del 5 de octubre en Argentina
                "fetched_at": "2026-10-06T01:30:00+00:00",
                "payload": {"results": [{"price": 1999.5, "title": "Ñandú"}]},
            }
        ]

        PostgresSink(conn).write_batch(records, "r1")

        [(sql, row, depth)] = conn.sql("insert into bronze.api_responses")
        self.assertEqual(depth, 1)  # dentro de la transacción
        self.assertEqual(row[1].isoformat(), "2026-10-05")
        self.assertEqual(json.loads(row[8])["results"][0]["title"], "Ñandú")

    def test_lote_vacio_no_toca_la_base(self):
        conn = FakeConn()
        PostgresSink(conn).write_batch([], "r1")
        self.assertEqual(conn.statements, [])

    def test_descarta_el_caracter_nulo_que_jsonb_no_acepta(self):
        conn = FakeConn()
        record = {
            "run_id": "r",
            "endpoint": "e",
            "path": "p",
            "params": {},
            "status": 200,
            "product_id": None,
            "fetched_at": "2026-10-05T12:00:00+00:00",
            "payload": {"name": "a\u0000b"},
        }
        PostgresSink(conn).write_batch([record], "r")
        payload = conn.sql("insert into bronze")[0][1][8]
        self.assertEqual(json.loads(payload)["name"], "ab")


class PostgresTokenStoreTests(unittest.TestCase):
    def test_load_sin_fila_devuelve_none(self):
        self.assertIsNone(PostgresTokenStore(FakeConn()).load())

    def test_load_y_save(self):
        expires = datetime(2026, 10, 6, tzinfo=UTC)
        conn = FakeConn({r"from ops\.auth_tokens": [("A", "R", expires, 7)]})
        store = PostgresTokenStore(conn)

        self.assertEqual(store.load(), Tokens("A", "R", expires, 7))
        store.save(Tokens("A2", "R2", expires, 7))
        [(sql, params, _)] = conn.sql("insert into ops.auth_tokens")
        self.assertIn("on conflict (id) do update", sql)
        self.assertEqual(params[:2], ("A2", "R2"))

    def test_el_lock_toma_un_advisory_lock_dentro_de_una_transaccion(self):
        conn = FakeConn()
        with PostgresTokenStore(conn).lock():
            pass
        [(_, _, depth)] = conn.sql("pg_advisory_xact_lock")
        self.assertEqual(depth, 1)


class ConcurrentRefreshTests(unittest.TestCase):
    """Dos corridas a la vez: la segunda tiene que reusar el token que renovó la primera."""

    def test_reusa_el_token_renovado_por_otra_corrida(self):
        tmp = Path(tempfile.mkdtemp())
        fresh = Tokens("ACCESS-OTRA", "REFRESH-OTRA", datetime.now(UTC) + timedelta(hours=6), 1)

        class Store:
            def __init__(self):
                self.current = valid_tokens(minutes=60)
                self.saved = []

            def load(self):
                return self.current

            def save(self, tokens):
                self.saved.append(tokens)

            @contextmanager
            def lock(self):
                # Mientras esta corrida esperaba el lock, otra renovó y guardó un token nuevo
                self.current = fresh
                yield

        store = Store()
        session = FakeSession().add(
            "POST", lambda u: u.endswith("/oauth/token"), FakeResponse(200, oauth_body())
        )
        manager = TokenManager(settings(tmp), store, session)
        manager.get_access_token()  # carga el token viejo en memoria

        self.assertEqual(manager.refresh(), fresh)
        self.assertEqual(session.calls, [])  # no llamó a MercadoLibre
        self.assertEqual(store.saved, [])


class PostgresTrackedProductsTests(unittest.TestCase):
    def test_vacia_necesita_refresco(self):
        conn = FakeConn({r"count\(\*\)": [(0, None, None)]})
        self.assertTrue(PostgresTrackedProducts(conn).needs_refresh("fp", timedelta(days=7)))

    def test_refresco_por_cambio_de_watchlist_y_por_antiguedad(self):
        recent = datetime.now(UTC) - timedelta(days=1)
        conn = FakeConn({r"count\(\*\)": [(3, recent, ["fp"])]})
        store = PostgresTrackedProducts(conn)
        self.assertFalse(store.needs_refresh("fp", timedelta(days=7)))
        self.assertTrue(store.needs_refresh("otro", timedelta(days=7)))
        self.assertTrue(store.needs_refresh("fp", timedelta(days=7), now=recent + timedelta(days=8)))

    def test_save_reemplaza_la_lista_en_una_transaccion(self):
        conn = FakeConn()
        PostgresTrackedProducts(conn).save(
            [
                TrackedProduct("P1", "iPhone", "MLA-CELLPHONES", "iphone-15"),
                TrackedProduct("P2", "S24", None, "s24"),
            ],
            "fp",
        )
        delete = conn.sql("delete from ops.tracked_products")
        inserts = conn.sql("insert into ops.tracked_products")
        self.assertEqual(delete[0][2], 1)
        self.assertEqual([row[0] for _, row, depth in inserts if depth == 1], ["P1", "P2"])

    def test_products(self):
        conn = FakeConn({r"select product_id": [("P1", "iPhone", "MLA-CELLPHONES", "iphone-15")]})
        self.assertEqual(
            PostgresTrackedProducts(conn).products(),
            [TrackedProduct("P1", "iPhone", "MLA-CELLPHONES", "iphone-15")],
        )


class PostgresRunLogTests(unittest.TestCase):
    def test_inserta_al_empezar_y_actualiza_al_terminar(self):
        conn = FakeConn()
        runs = PostgresRunLog(conn)
        summary = {"run_id": "r1", "started_at": "2026-10-05T12:00:00+00:00", "status": "running"}
        runs.start(summary)
        summary |= {"status": "failed", "error": "ApiError: 500", "http": {"requests": 3}}
        runs.finish(summary)

        self.assertEqual(conn.sql("insert into ops.pipeline_runs")[0][1], ("r1", summary["started_at"]))
        params = conn.sql("update ops.pipeline_runs")[0][1]
        self.assertEqual(params[2], "failed")
        self.assertEqual(json.loads(params[8]), {"requests": 3})
        self.assertEqual(params[-1], "r1")


if __name__ == "__main__":
    unittest.main()
