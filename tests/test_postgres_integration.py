"""
Tests de integración contra un Postgres real.

Se saltean si no está definida TEST_DATABASE_URL. Usar SIEMPRE una base de prueba (por ejemplo,
una rama "dev" de Neon o un Postgres local): los tests vacían las tablas de bronze y ops.

    $env:TEST_DATABASE_URL = "postgresql://..."     (PowerShell)
    python -m unittest tests.test_postgres_integration -v
"""

import os
import unittest
from datetime import UTC, datetime, timedelta

TEST_URL = os.environ.get("TEST_DATABASE_URL")

try:
    import psycopg  # noqa: F401

    HAS_PSYCOPG = True
except ImportError:
    HAS_PSYCOPG = False


@unittest.skipUnless(
    TEST_URL and HAS_PSYCOPG, "definí TEST_DATABASE_URL para correr los tests contra Postgres"
)
class PostgresIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from extract.config import load_settings

        if (
            os.environ.get("DATABASE_URL") == TEST_URL
            or getattr(load_settings(), "database_url", None) == TEST_URL
        ):
            raise unittest.SkipTest(
                "TEST_DATABASE_URL es la misma base que DATABASE_URL: no se vacían datos reales"
            )

        from extract import db

        cls.conn = db.connect(TEST_URL)
        db.migrate(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()

    def setUp(self):
        self.conn.execute(
            "truncate bronze.api_responses, ops.auth_tokens, ops.tracked_products, ops.pipeline_runs"
        )

    def test_migrar_dos_veces_no_falla_ni_reaplica(self):
        from extract import db

        self.assertEqual(db.migrate(self.conn), [])
        versions = [r[0] for r in self.conn.execute("select version from ops.schema_migrations").fetchall()]
        self.assertIn("001_init", versions)

    def test_sink_guarda_el_json_consultable(self):
        from extract.sinks import PostgresSink

        record = {
            "run_id": "r1",
            "endpoint": "/products/{id}/items",
            "path": "/products/MLA1/items",
            "params": {},
            "status": 200,
            "product_id": "MLA1",
            "fetched_at": "2026-10-06T01:30:00+00:00",
            "payload": {"results": [{"item_id": "MLA9", "price": 1506043.15}]},
        }
        PostgresSink(self.conn).write_batch([record, record | {"product_id": "MLA2"}], "r1")

        rows = self.conn.execute(
            "select snapshot_date, (payload->'results'->0->>'price')::numeric"
            " from bronze.api_responses order by product_id"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0].isoformat(), "2026-10-05")  # hora de Argentina
        self.assertEqual(str(rows[0][1]), "1506043.15")

    def test_tokens_ida_y_vuelta_y_lock(self):
        from extract.auth import PostgresTokenStore, Tokens

        store = PostgresTokenStore(self.conn)
        self.assertIsNone(store.load())
        expires = datetime.now(UTC).replace(microsecond=0) + timedelta(hours=6)
        store.save(Tokens("A", "R", expires, 533182444))
        with store.lock():
            store.save(Tokens("A2", "R2", expires, 533182444))
        self.assertEqual(store.load(), Tokens("A2", "R2", expires, 533182444))

    def test_productos_seguidos(self):
        from extract.discovery import PostgresTrackedProducts, TrackedProduct

        store = PostgresTrackedProducts(self.conn)
        self.assertTrue(store.needs_refresh("fp", timedelta(days=7)))
        store.save([TrackedProduct("P1", "iPhone 15", "MLA-CELLPHONES", "iphone-15")], "fp")
        self.assertFalse(store.needs_refresh("fp", timedelta(days=7)))
        self.assertTrue(store.needs_refresh("otro", timedelta(days=7)))
        store.save([TrackedProduct("P2", "S24", None, "s24")], "fp")
        self.assertEqual([p.product_id for p in store.products()], ["P2"])

    def test_registro_de_corridas(self):
        from extract.runlog import PostgresRunLog

        runs = PostgresRunLog(self.conn)
        summary = {"run_id": "r1", "started_at": datetime.now(UTC).isoformat(), "status": "running"}
        runs.start(summary)
        runs.finish(
            summary | {"status": "success", "duration_s": 12.3, "listings": 245, "http": {"requests": 23}}
        )

        status, listings, requests = self.conn.execute(
            "select status, listings, (http->>'requests')::int from ops.pipeline_runs where run_id = 'r1'"
        ).fetchone()
        self.assertEqual((status, listings, requests), ("success", 245, 23))


if __name__ == "__main__":
    unittest.main()
