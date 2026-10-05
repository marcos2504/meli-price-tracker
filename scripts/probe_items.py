"""
Fase 0 - Paso 2b: probar si se puede leer el detalle de ítems y productos conocidos.

Highlights y search están bloqueados para apps no certificadas. Este script prueba
los endpoints que necesitaríamos para una watchlist de productos elegidos a mano.

Uso:
    python scripts/probe_items.py                      # busca "iphone 15" y prueba con los resultados
    python scripts/probe_items.py --query "ps5"        # otra búsqueda
    python scripts/probe_items.py MLA1234567890 --product MLA12345678   # IDs puntuales

Cómo conseguir los IDs desde la web de MercadoLibre:
    - Publicación:  articulo.mercadolibre.com.ar/MLA-1234567890-...  ->  MLA1234567890
    - Producto de catálogo:  mercadolibre.com.ar/.../p/MLA12345678    ->  MLA12345678
"""

import argparse
import json
import os
import sys

import requests

BASE = "https://api.mercadolibre.com"
CATEGORY = os.environ.get("ML_CATEGORY", "MLA1055")


def get_token() -> str:
    token = os.environ.get("ML_ACCESS_TOKEN")
    if token:
        return token
    with open(".tokens.json") as f:
        return json.load(f)["access_token"]


def probe(s: requests.Session, name: str, path: str, params: dict | None = None):
    r = s.get(f"{BASE}{path}", params=params, timeout=30)
    tag = "OK  " if r.ok else "FAIL"
    print(f"[{tag}] {r.status_code}  {name:<34} {path}")
    if not r.ok:
        print(f"         -> {r.text[:200]}")
    return r


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("items", nargs="*", help="IDs de publicaciones (MLA + números)")
    parser.add_argument("--product", action="append", default=[], help="ID de producto de catálogo")
    parser.add_argument("--query", default="iphone 15", help="Texto para la búsqueda en catálogo")
    parser.add_argument(
        "--domain", default="MLA-CELLPHONES", help="Dominio de catálogo para filtrar; vacío para no filtrar"
    )
    args = parser.parse_args()

    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {get_token()}", "accept": "application/json"})
    ok = {}

    print("Endpoints sin ID (descubrimiento alternativo)\n")
    r = probe(s, "Tendencias de la categoría", f"/trends/MLA/{CATEGORY}")
    ok["trends"] = r.ok
    if r.ok:
        keywords = [t.get("keyword") for t in r.json()[:5]]
        print(f"         primeras tendencias: {keywords}")

    params = {"status": "active", "site_id": "MLA", "q": args.query, "limit": 30}
    if args.domain:
        params["domain_id"] = args.domain
    r = probe(s, "Búsqueda en catálogo", "/products/search", params)
    ok["products_search"] = r.ok
    candidates = []
    if r.ok:
        body = r.json()
        results = body.get("results", [])
        total = (body.get("paging") or {}).get("total")
        print(f"         '{args.query}' (dominio {args.domain or 'sin filtro'}): {total} productos, {len(results)} en esta página")
        domains = sorted({p.get("domain_id") for p in results if p.get("domain_id")})
        print(f"         dominios en los resultados: {domains}")
        candidates = [p["id"] for p in results if p.get("id")]

    # Buscar productos con publicaciones activas: /products/{id}/items devuelve 404 si no hay vendedores
    if not args.product and candidates:
        print("\nBuscando productos con publicaciones activas...\n")
        con_vendedores = 0
        for pid in candidates:
            r = s.get(f"{BASE}/products/{pid}/items", timeout=30)
            if r.ok:
                con_vendedores += 1
                if len(args.product) < 3:
                    args.product.append(pid)
        print(f"         {con_vendedores} de {len(candidates)} productos tienen publicaciones activas")

    if args.items:
        print("\nPublicaciones\n")
        r = probe(s, "Detalle de una publicación", f"/items/{args.items[0]}")
        ok["item"] = r.ok
        if r.ok:
            d = r.json()
            print(f"         título: {d.get('title')}")
            print(f"         precio: {d.get('currency_id')} {d.get('price')} | estado: {d.get('status')}")
        r = probe(s, "Multiget de publicaciones", "/items", {"ids": ",".join(args.items[:20])})
        ok["multiget"] = r.ok
        if r.ok:
            codes = [x.get("code") for x in r.json()]
            print(f"         códigos por ítem: {codes}")

    for pid in args.product:
        print(f"\nProducto de catálogo {pid}\n")
        r = probe(s, "Detalle del producto", f"/products/{pid}")
        ok[f"product_{pid}"] = r.ok
        if r.ok:
            d = r.json()
            winner = d.get("buy_box_winner") or {}
            print(f"         nombre: {d.get('name')}")
            print(f"         precio ganador: {winner.get('currency_id')} {winner.get('price')}")
        r = probe(s, "Publicaciones del producto", f"/products/{pid}/items")
        ok[f"product_items_{pid}"] = r.ok
        if r.ok:
            body = r.json()
            listings = body.get("results", []) if isinstance(body, dict) else []
            prices = sorted(x["price"] for x in listings if x.get("price") is not None)
            paging = body.get("paging") if isinstance(body, dict) else None
            print(f"         {len(listings)} publicaciones; precios: {prices[:5]}{' ...' if len(prices) > 5 else ''}")
            if paging:
                print(f"         paginación: {paging}")
            if listings and "sample" not in ok:
                ok["sample"] = True
                os.makedirs("samples", exist_ok=True)
                with open("samples/product_items.json", "w", encoding="utf-8") as f:
                    json.dump(body, f, indent=2, ensure_ascii=False)
                print(f"         campos de una publicación: {sorted(listings[0].keys())}")
                print("         respuesta completa guardada en samples/product_items.json")

    ok.pop("sample", None)
    print("\nResumen:")
    for k, v in ok.items():
        print(f"   {'OK  ' if v else 'FAIL'}  {k}")


if __name__ == "__main__":
    main()