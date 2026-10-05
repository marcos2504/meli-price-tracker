"""
Descubrimiento de productos a seguir.

Cada entrada de `watchlist.toml` es una búsqueda en el catálogo filtrada por dominio y,
opcionalmente, por expresiones regulares sobre el modelo y el nombre del producto.
De los resultados se quedan los productos que hoy tienen vendedores activos, hasta
`max_products` por búsqueda. La lista resultante se guarda en `tracked_products.json`
y se refresca cuando tiene más de N días o cuando cambia la watchlist.

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
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def _compile(raw: dict, key: str) -> re.Pattern | None:
    pattern = raw.get(key)
    if not pattern:
        return None
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Regex inválida en '{raw.get('name')}'.{key}: {pattern!r} ({exc})") from exc


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


def load_watchlist(path: Path) -> list[WatchlistEntry]:
    data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    entries = []
    for raw in data.get("search", []):
        entries.append(
            WatchlistEntry(
                name=raw["name"],
                query=raw["query"],
                domain_id=raw.get("domain_id"),
                max_products=int(raw.get("max_products", 10)),
                include=_compile(raw, "include"),
                exclude=_compile(raw, "exclude"),
            )
        )
    if not entries:
        raise ValueError(f"La watchlist {path} no tiene búsquedas ([[search]])")
    return entries


def discover(client: MeliClient, entries: list[WatchlistEntry], site_id: str = "MLA") -> DiscoveryResult:
    result = DiscoveryResult(products=[], responses=[], items_by_product={})
    seen: set[str] = set()

    for entry in entries:
        kept = 0
        filtered = {"n": 0}
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


class TrackedProductsRegistry:
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