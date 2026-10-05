"""Tests de descubrimiento, precios y la corrida completa, con la API simulada."""

import json
import re
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from extract.auth import FileTokenStore, TokenManager
from extract.client import MeliClient
from extract.discovery import (
    TrackedProduct,
    TrackedProductsRegistry,
    WatchlistEntry,
    discover,
    load_watchlist,
)
from extract.main import run
from extract.prices import fetch_prices
from tests.fakes import FakeResponse, FakeSession, settings, valid_tokens

WATCHLIST = """
[[search]]
name = "iphone-15"
query = "iphone 15"
domain_id = "MLA-CELLPHONES"
max_products = 2

[[search]]
name = "otro"
query = "otro"
max_products = 1
"""

LISTINGS = {
    "paging": {"total": 2},
    "results": [{"item_id": "MLA9", "price": 100}, {"item_id": "MLA8", "price": 90}],
}


class FakeCatalog:
    """Simula el catálogo: productos con y sin vendedores, y búsqueda paginada."""

    def __init__(
        self,
        search_pages: dict[str, list[list[str]]],
        with_sellers: set[str],
        catalog_info: dict[str, tuple[str, str | None]] | None = None,
    ):
        self.search_pages = search_pages
        self.with_sellers = with_sellers
        self.catalog_info = catalog_info or {}  # product_id -> (nombre, atributo MODEL)
        self.session = FakeSession()  # solo se usa para registrar las llamadas

    def _search_result(self, pid: str) -> dict:
        name, model = self.catalog_info.get(pid, (f"Producto {pid}", None))
        attributes = [{"id": "MODEL", "value_name": model}] if model else []
        return {"id": pid, "name": name, "domain_id": "MLA-CELLPHONES", "attributes": attributes}

    def get(self, url, params=None, **kwargs):
        self.session.calls.append(("GET", url, {"params": params, **kwargs}))
        if url.endswith("/products/search"):
            pages = self.search_pages.get(params["q"], [[]])
            idx = params.get("offset", 0) // 30
            page = pages[idx] if idx < len(pages) else []
            total = sum(len(p) for p in pages)
            results = [self._search_result(pid) for pid in page]
            return FakeResponse(200, {"paging": {"total": total}, "results": results})
        m = re.search(r"/products/(\w+)/items$", url)
        if m:
            return FakeResponse(200, LISTINGS) if m.group(1) in self.with_sellers else FakeResponse(404, {})
        m = re.search(r"/products/(\w+)$", url)
        if m:
            return FakeResponse(200, {"id": m.group(1), "name": f"Detalle {m.group(1)}"})
        return FakeResponse(404, {})

    def post(self, url, **kwargs):
        return FakeResponse(400, {})

    def count(self, pattern: str) -> int:
        return sum(1 for _, url, _ in self.session.calls if re.search(pattern, url))


def make_client(catalog: FakeCatalog, tmp: Path) -> MeliClient:
    s = settings(tmp)
    store = FileTokenStore(s.tokens_path)
    store.save(valid_tokens())
    return MeliClient(TokenManager(s, store, catalog), session=catalog, sleep=lambda _: None)


class WatchlistTests(unittest.TestCase):
    def test_carga_la_watchlist(self):
        path = Path(tempfile.mkdtemp()) / "watchlist.toml"
        path.write_text(WATCHLIST, encoding="utf-8")
        entries = load_watchlist(path)
        self.assertEqual(entries[0], WatchlistEntry("iphone-15", "iphone 15", "MLA-CELLPHONES", 2))
        self.assertIsNone(entries[1].domain_id)

    def test_carga_filtros_regex(self):
        path = Path(tempfile.mkdtemp()) / "watchlist.toml"
        path.write_text(
            """
[[search]]
name = "s24"
query = "galaxy s24"
include = '\\bs24\\b'
exclude = '\\b(fe|ultra)\\b'
""",
            encoding="utf-8",
        )
        entry = load_watchlist(path)[0]
        self.assertTrue(entry.include.search("Samsung GALAXY S24"))  # sin distinguir mayúsculas
        self.assertTrue(entry.exclude.search("S24 FE"))

    def test_regex_invalida_da_un_error_claro(self):
        path = Path(tempfile.mkdtemp()) / "watchlist.toml"
        path.write_text('[[search]]\nname = "x"\nquery = "x"\ninclude = "s24("\n', encoding="utf-8")
        with self.assertRaises(ValueError) as ctx:
            load_watchlist(path)
        self.assertIn("'x'.include", str(ctx.exception))

    def test_watchlist_vacia_falla(self):
        path = Path(tempfile.mkdtemp()) / "watchlist.toml"
        path.write_text("# nada\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_watchlist(path)


class DiscoveryTests(unittest.TestCase):
    def test_se_queda_con_productos_con_vendedores_hasta_el_maximo(self):
        catalog = FakeCatalog({"iphone 15": [["P1", "P2", "P3", "P4"]]}, with_sellers={"P2", "P3", "P4"})
        client = make_client(catalog, Path(tempfile.mkdtemp()))

        result = discover(client, [WatchlistEntry("iphone-15", "iphone 15", "MLA-CELLPHONES", 2)])

        self.assertEqual([p.product_id for p in result.products], ["P2", "P3"])
        self.assertEqual(result.products[0].name, "Detalle P2")
        self.assertEqual(set(result.items_by_product), {"P2", "P3"})
        self.assertEqual(catalog.count(r"/products/P4"), 0)  # no revisa más de lo necesario

    def test_recorre_paginas_y_no_repite_productos_entre_busquedas(self):
        # La primera página de "a" no tiene productos con vendedores: hay que ir a la segunda
        pages = {"a": [[f"A{i}" for i in range(30)], ["P1"]], "b": [["P1", "B1"]]}
        catalog = FakeCatalog(pages, with_sellers={"P1", "B1"})
        client = make_client(catalog, Path(tempfile.mkdtemp()))

        result = discover(client, [WatchlistEntry("a", "a", None, 11), WatchlistEntry("b", "b", None, 1)])

        self.assertEqual([p.product_id for p in result.products], ["P1", "B1"])
        self.assertEqual(catalog.count(r"/products/search"), 3)  # 2 páginas de "a" + 1 de "b"

    def test_filtro_regex_usa_modelo_y_nombre_y_evita_llamadas(self):
        info = {
            "A": ("Samsung Galaxy S24 256GB", "Galaxy S24"),
            "B": ("Samsung Galaxy S24 Fe 128gb", "S24 FE"),
            "C": ("Samsung Galaxy S24 Plus 512gb", "S24"),  # el modelo dice S24, el nombre dice Plus
            "D": ("Samsung Galaxy S24 Negro", "S24 (eSIM)"),
            "E": ("Celular Samsung Galaxy S7", "Samsung Galaxy S7"),
        }
        catalog = FakeCatalog({"s24": [list(info)]}, with_sellers=set(info), catalog_info=info)
        client = make_client(catalog, Path(tempfile.mkdtemp()))
        entry = WatchlistEntry(
            "s24",
            "s24",
            None,
            10,
            include=re.compile(r"\bs24\b", re.I),
            exclude=re.compile(r"\b(fe|plus)\b", re.I),
        )

        result = discover(client, [entry])

        self.assertEqual([p.product_id for p in result.products], ["A", "D"])
        # A los descartados por el filtro no se les piden precios
        self.assertEqual(catalog.count(r"/products/(B|C|E)/items"), 0)

    def test_filtro_estricto_no_recorre_paginas_sin_fin(self):
        pages = [[f"X{p}_{i}" for i in range(30)] for p in range(50)]  # 1500 productos que no matchean
        catalog = FakeCatalog({"q": pages}, with_sellers=set())
        client = make_client(catalog, Path(tempfile.mkdtemp()))
        entry = WatchlistEntry("q", "q", None, 5, include=re.compile("nunca"))

        result = discover(client, [entry])

        self.assertEqual(result.products, [])
        # offsets 0, 30, 60 y 90: nunca pide más allá del límite de 100 de la API
        searches = [c for c in catalog.session.calls if c[1].endswith("/products/search")]
        offsets = [c[2]["params"]["offset"] for c in searches]
        self.assertEqual(offsets, [0, 30, 60, 90])

    def test_400_en_pagina_posterior_corta_la_busqueda_sin_tirar_la_corrida(self):
        catalog = FakeCatalog({"q": [[f"A{i}" for i in range(30)], ["B1"]]}, with_sellers={"A0", "A1"})
        original_get = catalog.get

        def get_with_limit(url, params=None, **kwargs):
            if url.endswith("/products/search") and params.get("offset", 0) > 0:
                catalog.session.calls.append(("GET", url, {"params": params}))
                return FakeResponse(400, {"error": "bad_request", "message": "offset"})
            return original_get(url, params=params, **kwargs)

        catalog.get = get_with_limit
        client = make_client(catalog, Path(tempfile.mkdtemp()))

        result = discover(client, [WatchlistEntry("q", "q", None, 5)])

        self.assertEqual([p.product_id for p in result.products], ["A0", "A1"])

    def test_filtra_por_dominio(self):
        catalog = FakeCatalog({"iphone 15": [["P1"]]}, with_sellers={"P1"})
        client = make_client(catalog, Path(tempfile.mkdtemp()))
        discover(client, [WatchlistEntry("x", "iphone 15", "MLA-CELLPHONES", 1)])
        search_call = next(c for c in catalog.session.calls if c[1].endswith("/products/search"))
        self.assertEqual(search_call[2]["params"]["domain_id"], "MLA-CELLPHONES")


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = TrackedProductsRegistry(Path(tempfile.mkdtemp()) / "tracked.json")

    def test_sin_archivo_necesita_refresco(self):
        self.assertTrue(self.registry.needs_refresh("abc", timedelta(days=7)))

    def test_refresco_por_antiguedad_y_por_cambio_de_watchlist(self):
        self.registry.save([TrackedProduct("P1", "n", None, "s")], "abc")
        self.assertFalse(self.registry.needs_refresh("abc", timedelta(days=7)))
        self.assertTrue(self.registry.needs_refresh("otra", timedelta(days=7)))
        later = datetime.now(UTC) + timedelta(days=8)
        self.assertTrue(self.registry.needs_refresh("abc", timedelta(days=7), now=later))


class PricesTests(unittest.TestCase):
    def test_reusa_lo_ya_consultado_y_cuenta_sin_vendedores(self):
        catalog = FakeCatalog({}, with_sellers={"P1"})
        client = make_client(catalog, Path(tempfile.mkdtemp()))
        prefetched = {"P1": client.get("/products/P1/items")}

        result = fetch_prices(client, ["P1", "P2"], prefetched)

        self.assertEqual((result.with_sellers, result.without_sellers, result.listings), (1, 1, 2))
        self.assertEqual(catalog.count(r"/products/P1/items"), 1)  # no se volvió a pedir


class RunTests(unittest.TestCase):
    def test_corrida_completa_escribe_bronze_y_registro(self):
        tmp = Path(tempfile.mkdtemp())
        s = settings(tmp)
        s.watchlist_path.write_text(WATCHLIST, encoding="utf-8")
        FileTokenStore(s.tokens_path).save(valid_tokens())
        catalog = FakeCatalog({"iphone 15": [["P1", "P2", "P3"]], "otro": [["P9"]]}, {"P1", "P3", "P9"})

        with (
            mock.patch("extract.main.load_settings", return_value=s),
            mock.patch("requests.Session", return_value=catalog),
        ):
            first = run()
            second = run()  # al día siguiente: no redescubre, solo precios

        self.assertEqual(first["status"], "success", first.get("error"))
        self.assertTrue(first["discovery"])
        self.assertFalse(second["discovery"])
        self.assertEqual(first["products"], 3)
        self.assertEqual(first["listings"], 6)

        bronze = list((s.data_dir / "bronze").rglob("*.jsonl"))
        records = [json.loads(line) for f in bronze for line in f.read_text(encoding="utf-8").splitlines()]
        first_run = [r for r in records if r["run_id"] == first["run_id"]]
        price_records = [r for r in first_run if r["endpoint"] == "/products/{id}/items"]
        # Cada producto seguido aparece una sola vez con sus precios (sin duplicar lo del descubrimiento)
        self.assertEqual(sorted(r["product_id"] for r in price_records), ["P1", "P3", "P9"])
        self.assertTrue((s.data_dir / "runs" / f"{first['run_id']}.json").exists())

    def test_falla_de_autenticacion_queda_registrada(self):
        tmp = Path(tempfile.mkdtemp())
        s = settings(tmp)
        s.watchlist_path.write_text(WATCHLIST, encoding="utf-8")  # sin tokens guardados

        with mock.patch("extract.main.load_settings", return_value=s):
            summary = run()

        self.assertEqual(summary["status"], "failed")
        self.assertIn("bootstrap", summary["error"])


if __name__ == "__main__":
    unittest.main()