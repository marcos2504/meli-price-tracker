"""
Descubrimiento de productos a seguir. `watchlist.toml` admite dos tipos de entrada:

- `[[product]]`: un producto puntual, con el link de MercadoLibre o su ID. Se sigue siempre,
  tenga o no vendedores ese día. Es la forma más simple de agregar algo.
- `[[search]]`: una búsqueda en el catálogo filtrada por dominio y, opcionalmente, por
  expresiones regulares sobre el modelo y el nombre. De los resultados se quedan los productos
  que hoy tienen vendedores activos, hasta `max_products` por búsqueda.

La lista resultante se guarda (ops.tracked_products o tracked_products.json) y se refresca
cuando tiene más de N días o cuando cambia la watchlist.

Por qué regex y no el atributo MODEL exacto: en muchas marcas el atributo lo cargan los
vendedores sin formato fijo (el mismo Galaxy S24 aparece como "S24", "S24 (eSIM)",
"Galaxy S24" o "S24 Dual SIM"), así que un valor exacto se pierde la mitad de los productos.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from extract.client import ApiError, ApiResponse, MeliClient

log = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 30
CANDIDATES_PER_PRODUCT = 3  # cuántos candidatos que pasan el filtro revisar por cada producto pedido
MAX_SEARCH_OFFSET = 100  # límite de la API: /products/search rechaza offset > 100 (máximo 4 páginas de 30)


@dataclass(frozen=True)
class WatchlistEntry:
    name: str
    query: str
    domain_id: str | None
    max_products: int
    include: re.Pattern | None = None
    exclude: re.Pattern | None = None

    def matches(self, product: dict) -> bool:
        """Aplica include/exclude sobre el modelo y el nombre del producto (sin distinguir mayúsculas)."""
        text = match_text(product)
        if self.include and not self.include.search(text):
            return False
        return not (self.exclude and self.exclude.search(text))


def match_text(product: dict) -> str:
    attrs = {a.get("id"): a.get("value_name") for a in product.get("attributes") or []}
    return " ".join(filter(None, [attrs.get("MODEL"), product.get("name")]))


def compile_filter(raw: dict, key: str) -> re.Pattern | None:
    pattern = raw.get(key)
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Regex inválida en '{raw.get('name')}'.{key}: {pattern!r} ({exc})") from exc


@dataclass(frozen=True)
class PinnedProduct:
    """Producto agregado a mano en [[product]]. group es el nombre con el que aparece en el dashboard."""

    product_id: str
    group: str


DEFAULT_GROUP = "favoritos"

# Productos de catálogo: el ID va después de /p/ (…/apple-iphone-15/p/MLA1027172677)
_CATALOG_URL = re.compile(r"/p/([A-Z]{3}\d+)")
_PRODUCT_ID = re.compile(r"^[A-Z]{3}\d+$")
# Publicaciones sueltas: articulo.mercadolibre.com.ar/MLA-123… (no son productos de catálogo)
_LISTING_URL = re.compile(r"articulo\.mercadolibre|(^|/)[A-Z]{3}-\d+")


def parse_product_ref(value: str) -> str:
    """Devuelve el ID de producto de catálogo a partir de un link de MercadoLibre o del ID."""
    value = value.strip()
    if _PRODUCT_ID.match(value.upper()):
        return value.upper()
    match = _CATALOG_URL.search(value)
    if match:
        return match.group(1)
    if _LISTING_URL.search(value):
        raise ValueError(
            f"{value!r} es el link de una publicación, no de un producto de catálogo. "
            "Abrí el producto en MercadoLibre y copiá el link que tiene /p/MLA… "
            "(aparece al entrar desde la búsqueda o en 'Ver más opciones de compra')."
        )
    raise ValueError(f"No reconozco {value!r}: pegá el link del producto (con /p/MLA…) o su ID")


@dataclass
class TrackedProduct:
    product_id: str
    name: str
    domain_id: str | None
    search: str


@dataclass
class DiscoveryResult:
    products: list[TrackedProduct]
    responses: list[tuple[str, str | None, ApiResponse]]  # (endpoint, product_id, respuesta) para bronze
    items_by_product: dict[str, ApiResponse]  # precios ya obtenidos al validar, para no repetir llamadas
    rejected: dict[str, list[str]] = field(default_factory=dict)  # búsqueda -> nombres descartados


def _read(path: Path) -> dict:
    return tomllib.loads(Path(path).read_text(encoding="utf-8"))


def load_pinned(path: Path) -> list[PinnedProduct]:
    """Productos de las entradas [[product]], con `url` (link de MercadoLibre) o `id`."""
    pinned = []
    for i, raw in enumerate(_read(path).get("product", []), start=1):
        ref = raw.get("url") or raw.get("id")
        if not ref:
            raise ValueError(f"La entrada [[product]] número {i} no tiene url ni id")
        pinned.append(PinnedProduct(parse_product_ref(ref), raw.get("group", DEFAULT_GROUP)))
    return pinned


def load_watchlist(path: Path) -> list[WatchlistEntry]:
    data = _read(path)
    entries = []
    for raw in data.get("search", []):
        entries.append(
            WatchlistEntry(
                name=raw["name"],
                query=raw["query"],
                domain_id=raw.get("domain_id"),
                max_products=int(raw.get("max_products", 10)),
                include=compile_filter(raw, "include"),
                exclude=compile_filter(raw, "exclude"),
            )
        )
    if not entries and not data.get("product"):
        raise ValueError(f"La watchlist {path} está vacía: no tiene [[product]] ni [[search]]")
    return entries


def discover(
    client: MeliClient,
    entries: list[WatchlistEntry],
    site_id: str = "MLA",
    pinned: list[PinnedProduct] = (),
) -> DiscoveryResult:
    result = DiscoveryResult(products=[], responses=[], items_by_product={})
    seen: set[str] = set()

    # Primero los productos puestos a mano: se siguen aunque hoy no tengan vendedores
    for item in pinned:
        if item.product_id in seen:
            continue
        seen.add(item.product_id)
        detail = client.get(f"/products/{item.product_id}")
        if not detail.ok:
            log.warning("Producto %s de [[product]]: no existe en el catálogo, se ignora", item.product_id)
            continue
        result.responses.append(("/products/{id}", item.product_id, detail))
        result.products.append(
            TrackedProduct(
                product_id=item.product_id,
                name=detail.data.get("name", ""),
                domain_id=detail.data.get("domain_id"),
                search=item.group,
            )
        )
    if pinned:
        log.info("Productos puestos a mano: %d", len(result.products))

    for entry in entries:
        kept = 0
        filtered = {"n": 0, "names": []}
        for candidate in _search_candidates(client, entry, site_id, result, filtered):
            pid = candidate["id"]
            if pid in seen:
                continue
            seen.add(pid)

            items = client.get(f"/products/{pid}/items")
            if not items.ok:
                continue  # 404: el producto no tiene vendedores hoy
            # Los precios de los productos que quedan los registra fetch_prices, sin duplicar en bronze

            detail = client.get(f"/products/{pid}")
            result.responses.append(("/products/{id}", pid, detail))
            name = (detail.data or {}).get("name") if detail.ok else candidate.get("name")

            result.products.append(
                TrackedProduct(
                    product_id=pid,
                    name=name or candidate.get("name", ""),
                    domain_id=candidate.get("domain_id"),
                    search=entry.name,
                )
            )
            result.items_by_product[pid] = items
            kept += 1
            if kept >= entry.max_products:
                break

        result.rejected[entry.name] = filtered["names"]
        log.info(
            "Búsqueda '%s': %d productos con vendedores activos (%d descartados por el filtro)",
            entry.name,
            kept,
            filtered["n"],
        )

    return result


def _search_candidates(
    client: MeliClient, entry: WatchlistEntry, site_id: str, result: DiscoveryResult, filtered: dict
):
    """Recorre las páginas de /products/search y entrega los candidatos que pasan el filtro."""
    max_candidates = entry.max_products * CANDIDATES_PER_PRODUCT
    offset = 0
    yielded = 0

    while yielded < max_candidates and offset <= MAX_SEARCH_OFFSET:
        params = {
            "status": "active",
            "site_id": site_id,
            "q": entry.query,
            "limit": SEARCH_PAGE_SIZE,
            "offset": offset,
        }
        if entry.domain_id:
            params["domain_id"] = entry.domain_id

        try:
            resp = client.get("/products/search", params)
        except ApiError as exc:
            # Un 400 en una página posterior (por ejemplo, un límite de paginación nuevo) corta esta
            # búsqueda pero no la corrida: los productos ya encontrados siguen sirviendo.
            if exc.status == 400 and offset > 0:
                log.warning("Búsqueda '%s': la API rechazó offset=%d", entry.name, offset)
                return
            raise
        result.responses.append(("/products/search", None, resp))
        if not resp.ok:
            return

        page = resp.data.get("results", [])
        for product in page:
            if not product.get("id"):
                continue
            if not entry.matches(product):
                filtered["n"] += 1
                filtered["names"].append(product.get("name", product["id"]))
                continue
            yield product
            yielded += 1
            if yielded >= max_candidates:
                return

        total = (resp.data.get("paging") or {}).get("total", 0)
        offset += SEARCH_PAGE_SIZE
        if not page or offset >= total:
            return


# --- Lista de productos seguidos ---------------------------------------------------------


def watchlist_fingerprint(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]


class TrackedProductsStore(Protocol):
    def needs_refresh(self, fingerprint: str, max_age: timedelta, now: datetime | None = None) -> bool: ...
    def products(self) -> list[TrackedProduct]: ...
    def save(self, products: list[TrackedProduct], fingerprint: str) -> None: ...


class TrackedProductsRegistry:
    """Lista de productos seguidos en un JSON local (modo sin base de datos)."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict | None:
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text(encoding="utf-8"))

    def needs_refresh(self, fingerprint: str, max_age: timedelta, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        data = self.load()
        if not data or not data.get("products"):
            return True
        if data.get("watchlist_fingerprint") != fingerprint:
            return True
        return now - datetime.fromisoformat(data["refreshed_at"]) > max_age

    def products(self) -> list[TrackedProduct]:
        data = self.load() or {}
        return [TrackedProduct(**p) for p in data.get("products", [])]

    def save(self, products: list[TrackedProduct], fingerprint: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "refreshed_at": datetime.now(UTC).isoformat(),
            "watchlist_fingerprint": fingerprint,
            "products": [asdict(p) for p in products],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


class PostgresTrackedProducts:
    """Lista de productos seguidos en ops.tracked_products. Se reemplaza completa en cada descubrimiento."""

    def __init__(self, conn):
        self.conn = conn

    def needs_refresh(self, fingerprint: str, max_age: timedelta, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        count, refreshed_at, fingerprints = self.conn.execute(
            "select count(*), min(refreshed_at), array_agg(distinct watchlist_fingerprint)"
            " from ops.tracked_products"
        ).fetchone()
        if not count:
            return True
        if fingerprints != [fingerprint]:
            return True
        return now - refreshed_at > max_age

    def products(self) -> list[TrackedProduct]:
        rows = self.conn.execute(
            "select product_id, name, domain_id, search from ops.tracked_products order by search, product_id"
        ).fetchall()
        return [TrackedProduct(*row) for row in rows]

    def save(self, products: list[TrackedProduct], fingerprint: str) -> None:
        now = datetime.now(UTC)
        rows = [(p.product_id, p.name, p.domain_id, p.search, now, fingerprint) for p in products]
        with self.conn.transaction():
            self.conn.execute("delete from ops.tracked_products")
            with self.conn.cursor() as cur:
                cur.executemany(
                    "insert into ops.tracked_products"
                    " (product_id, name, domain_id, search, refreshed_at, watchlist_fingerprint)"
                    " values (%s, %s, %s, %s, %s, %s)",
                    rows,
                )
