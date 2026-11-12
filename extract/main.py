"""
Punto de entrada del extractor.

    python -m extract db-init                 crea las tablas e importa el token local (una sola vez)
    python -m extract bootstrap [--pkce]      autorización OAuth inicial
    python -m extract run [--force-discovery] descubrimiento (si toca) y precios del día
    python -m extract preview [--query ...]   qué productos encontraría la watchlist, sin guardar nada

Si DATABASE_URL está definida, todo se guarda en Postgres (bronze, tokens, productos seguidos y
registro de corridas). Si no, en archivos locales dentro de data/.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from extract.auth import FileTokenStore, PostgresTokenStore, TokenManager, TokenStore, bootstrap
from extract.client import MeliClient
from extract.config import Settings, load_settings
from extract.discovery import (
    PostgresTrackedProducts,
    TrackedProductsRegistry,
    TrackedProductsStore,
    WatchlistEntry,
    compile_filter,
    discover,
    load_pinned,
    load_watchlist,
    watchlist_fingerprint,
)
from extract.prices import fetch_prices
from extract.runlog import FileRunLog, PostgresRunLog, RunLog
from extract.sinks import LocalJsonlSink, PostgresSink, Sink, to_record

log = logging.getLogger("extract")


@dataclass
class Backend:
    """Dónde guarda el pipeline cada cosa. Todo junto en archivos locales o todo junto en Postgres."""

    name: str
    sink: Sink
    tokens: TokenStore
    products: TrackedProductsStore
    runs: RunLog
    close: Callable[[], None] = field(default=lambda: None)


def open_backend(settings: Settings) -> Backend:
    if settings.database_url:
        from extract import db  # importado acá: el modo local no necesita psycopg

        conn = db.connect(settings.database_url)
        db.migrate(conn)
        return Backend(
            name="postgres",
            sink=PostgresSink(conn),
            tokens=PostgresTokenStore(conn),
            products=PostgresTrackedProducts(conn),
            runs=PostgresRunLog(conn),
            close=conn.close,
        )
    return Backend(
        name="local",
        sink=LocalJsonlSink(settings.data_dir),
        tokens=FileTokenStore(settings.tokens_path),
        products=TrackedProductsRegistry(settings.data_dir / "tracked_products.json"),
        runs=FileRunLog(settings.data_dir),
    )


def run(
    force_discovery: bool = False, backend: Backend | None = None, settings: Settings | None = None
) -> dict:
    settings = settings or load_settings()
    owns_backend = backend is None
    backend = backend or open_backend(settings)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(UTC)
    summary: dict = {
        "run_id": run_id,
        "backend": backend.name,
        "started_at": started.isoformat(),
        # En GitHub Actions: schedule (cron) o workflow_dispatch (manual). Fuera de Actions: local
        "triggered_by": os.environ.get("GITHUB_EVENT_NAME", "local"),
        "status": "running",
    }

    tokens = TokenManager(settings, backend.tokens)
    client = MeliClient(tokens, base_url=settings.api_base)
    sink, registry = backend.sink, backend.products

    try:
        backend.runs.start(summary)

        # 1. Descubrimiento (semanal, o si cambió la watchlist)
        fingerprint = watchlist_fingerprint(settings.watchlist_path)
        max_age = timedelta(days=settings.discovery_max_age_days)
        prefetched = {}
        if force_discovery or registry.needs_refresh(fingerprint, max_age):
            log.info("Refrescando la lista de productos desde la watchlist")
            found = discover(
                client,
                load_watchlist(settings.watchlist_path),
                settings.site_id,
                pinned=load_pinned(settings.watchlist_path),
            )
            sink.write_batch([to_record(r, run_id, ep, pid) for ep, pid, r in found.responses], run_id)
            registry.save(found.products, fingerprint)
            prefetched = found.items_by_product
            summary["discovery"] = True
        else:
            summary["discovery"] = False

        products = registry.products()
        if not products:
            raise RuntimeError("La lista de productos está vacía: revisá watchlist.toml")

        # 2. Precios del día
        prices = fetch_prices(client, [p.product_id for p in products], prefetched)
        sink.write_batch(
            [to_record(r, run_id, "/products/{id}/items", pid) for pid, r in prices.responses], run_id
        )

        summary |= {
            "status": "success",
            "products": len(products),
            "products_with_sellers": prices.with_sellers,
            "products_without_sellers": prices.without_sellers,
            "listings": prices.listings,
        }
    except Exception as exc:  # cualquier falla queda registrada en el log de corridas
        summary |= {"status": "failed", "error": f"{exc.__class__.__name__}: {exc}"}
        log.error("La corrida falló: %s", exc)
    finally:
        summary |= {
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1),
            "http": asdict(client.stats),
        }
        try:
            backend.runs.finish(summary)
        except Exception as exc:
            log.error("No se pudo guardar el registro de la corrida: %s", exc)
        if owns_backend:
            backend.close()

    return summary


def db_init(settings: Settings) -> dict:
    """Crea las tablas y, si hay un .tokens.json local, lo mueve a ops.auth_tokens."""
    if not settings.database_url:
        raise RuntimeError("Falta DATABASE_URL en el archivo .env")
    from extract import db

    with db.connect(settings.database_url) as conn:
        applied = db.migrate(conn)
        result = {"migrations_applied": applied, "tokens_imported": False}

        pg_store, file_store = PostgresTokenStore(conn), FileTokenStore(settings.tokens_path)
        local_tokens = file_store.load()
        if pg_store.load() is None and local_tokens is not None:
            pg_store.save(local_tokens)
            # Renombrar el archivo evita usar por error un refresh token que la base va a rotar
            backup = settings.tokens_path.with_name(settings.tokens_path.name + ".migrated")
            settings.tokens_path.replace(backup)
            result |= {"tokens_imported": True, "tokens_backup": str(backup)}
    return result


def preview(settings: Settings, args) -> None:
    """Corre el descubrimiento sin guardar nada y muestra qué productos quedarían y cuáles se descartan.
    Con --query prueba una búsqueda nueva antes de agregarla a watchlist.toml."""
    if args.query:
        raw = {"name": "prueba", "include": args.include, "exclude": args.exclude}
        entries = [
            WatchlistEntry(
                "prueba",
                args.query,
                args.domain,
                args.max_products,
                include=compile_filter(raw, "include"),
                exclude=compile_filter(raw, "exclude"),
            )
        ]
        pinned = []
    else:
        entries, pinned = load_watchlist(settings.watchlist_path), load_pinned(settings.watchlist_path)

    backend = open_backend(settings)
    try:
        client = MeliClient(TokenManager(settings, backend.tokens), base_url=settings.api_base)
        found = discover(client, entries, settings.site_id, pinned=pinned)
    finally:
        backend.close()

    groups = list(dict.fromkeys([p.group for p in pinned] + [e.name for e in entries]))
    for group in groups:
        products = [p for p in found.products if p.search == group]
        print(f"\n== {group}: {len(products)} productos")
        for p in products:
            items = found.items_by_product.get(p.product_id)
            sellers = ((items.data or {}).get("paging") or {}).get("total", "?") if items else "-"
            print(f"  {p.product_id:<15} {str(sellers):>4} vend.  {p.domain_id or '':<22} {p.name}")
        rejected = found.rejected.get(group, [])
        if rejected:
            print(f"  -- descartados por include/exclude ({len(rejected)}):")
            for name in rejected[:15]:
                print(f"     {name}")
            if len(rejected) > 15:
                print(f"     … y {len(rejected) - 15} más")
    print("\nNo se guardó nada. Si la lista te sirve, agregá la búsqueda a watchlist.toml.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m extract", description="Extractor de precios de MercadoLibre"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("db-init", help="Crear las tablas en Postgres e importar el token local")

    p_boot = sub.add_parser("bootstrap", help="Autorización OAuth inicial")
    p_boot.add_argument(
        "--pkce", action="store_true", help="Usar PKCE (activarlo antes en la app del DevCenter)"
    )

    p_run = sub.add_parser("run", help="Descubrimiento (si toca) y precios del día")
    p_run.add_argument("--force-discovery", action="store_true", help="Refrescar la lista de productos ahora")

    p_prev = sub.add_parser("preview", help="Ver qué productos encontraría la watchlist, sin guardar nada")
    p_prev.add_argument("--query", help="Probar una búsqueda nueva en lugar de la watchlist")
    p_prev.add_argument("--domain", help="domain_id de la búsqueda de prueba, ej. MLA-CELLPHONES")
    p_prev.add_argument("--include", help="Regex que tienen que cumplir el modelo o el nombre")
    p_prev.add_argument("--exclude", help="Regex que descarta productos")
    p_prev.add_argument("--max-products", type=int, default=10)

    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        return _dispatch(args)
    except Exception as exc:  # errores de configuración o conexión: mensaje claro, sin traceback
        log.error("%s: %s", exc.__class__.__name__, exc)
        if args.verbose:
            raise
        return 1


def _dispatch(args) -> int:
    settings = load_settings()

    if args.command == "db-init":
        print(json.dumps(db_init(settings), indent=2, ensure_ascii=False))
        return 0

    if args.command == "bootstrap":
        backend = open_backend(settings)
        try:
            tokens = bootstrap(settings, backend.tokens, use_pkce=args.pkce)
        finally:
            backend.close()
        print(f"\nOK - tokens guardados en {backend.name}. Vencen: {tokens.expires_at.isoformat()}")
        return 0

    if args.command == "preview":
        preview(settings, args)
        return 0

    summary = run(force_discovery=args.force_discovery, settings=settings)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
