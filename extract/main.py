"""
Punto de entrada del extractor.

    python -m extract bootstrap [--pkce]    autorización inicial (una sola vez)
    python -m extract run [--force-discovery]
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from extract.auth import AuthError, FileTokenStore, TokenManager, bootstrap
from extract.client import ApiError, MeliClient
from extract.config import load_settings
from extract.discovery import TrackedProductsRegistry, discover, load_watchlist, watchlist_fingerprint
from extract.prices import fetch_prices
from extract.sinks import LocalJsonlSink, Sink, to_record

log = logging.getLogger("extract")


def run(force_discovery: bool = False, sink: Sink | None = None) -> dict:
    settings = load_settings()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    started = datetime.now(UTC)
    sink = sink or LocalJsonlSink(settings.data_dir)

    tokens = TokenManager(settings, FileTokenStore(settings.tokens_path))
    client = MeliClient(tokens, base_url=settings.api_base)
    registry = TrackedProductsRegistry(settings.data_dir / "tracked_products.json")

    summary: dict = {"run_id": run_id, "started_at": started.isoformat(), "status": "running"}
    try:
        # 1. Descubrimiento (semanal, o si cambió la watchlist)
        fingerprint = watchlist_fingerprint(settings.watchlist_path)
        max_age = timedelta(days=settings.discovery_max_age_days)
        prefetched = {}
        if force_discovery or registry.needs_refresh(fingerprint, max_age):
            log.info("Refrescando la lista de productos desde la watchlist")
            found = discover(client, load_watchlist(settings.watchlist_path), settings.site_id)
            sink.write_batch([to_record(r, run_id, ep, pid) for ep, pid, r in found.responses], run_id)
            registry.save(found.products, fingerprint)
            prefetched = found.items_by_product
            summary["discovery"] = True
        else:
            summary["discovery"] = False

        products = registry.products()
        if not products:
            raise RuntimeError("La lista de productos está vacía: revisá las búsquedas de watchlist.toml")

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
    except (AuthError, ApiError, RuntimeError) as exc:
        summary |= {"status": "failed", "error": str(exc)}
        log.error("La corrida falló: %s", exc)
    finally:
        summary |= {
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_s": round((datetime.now(UTC) - started).total_seconds(), 1),
            "http": asdict(client.stats),
        }
        _save_run_log(settings.data_dir, summary)

    return summary


def _save_run_log(data_dir, summary: dict) -> None:
    """Registro de la corrida. En la Fase 2 pasa a la tabla ops.pipeline_runs."""
    folder = data_dir / "runs"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{summary['run_id']}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m extract", description="Extractor de precios de MercadoLibre"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_boot = sub.add_parser("bootstrap", help="Autorización OAuth inicial")
    p_boot.add_argument(
        "--pkce", action="store_true", help="Usar PKCE (activarlo antes en la app del DevCenter)"
    )

    p_run = sub.add_parser("run", help="Descubrimiento (si toca) y precios del día")
    p_run.add_argument("--force-discovery", action="store_true", help="Refrescar la lista de productos ahora")

    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "bootstrap":
        settings = load_settings()
        tokens = bootstrap(settings, FileTokenStore(settings.tokens_path), use_pkce=args.pkce)
        print(f"\nOK - tokens guardados. user_id: {tokens.user_id} | vence: {tokens.expires_at.isoformat()}")
        return 0

    summary = run(force_discovery=args.force_discovery)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["status"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
