"""
Fase 0 - Paso 2: probar los endpoints que vamos a usar y registrar qué responde cada uno.

Toma el access token de la variable ML_ACCESS_TOKEN (Actions) o de .tokens.json (local).
Correrlo local y en Actions: si local da 200 y Actions 403, el bloqueo es por IP.
"""

import json
import os
import sys

import requests

CATEGORY = os.environ.get("ML_CATEGORY", "MLA1055")  # Celulares y Smartphones
BASE = "https://api.mercadolibre.com"


def get_token() -> str:
    token = os.environ.get("ML_ACCESS_TOKEN")
    if token:
        return token
    with open(".tokens.json") as f:
        return json.load(f)["access_token"]


def probe(session: requests.Session, name: str, path: str, params: dict | None = None):
    r = session.get(f"{BASE}{path}", params=params, timeout=30)
    ok = "OK  " if r.ok else "FAIL"
    print(f"[{ok}] {r.status_code}  {name:<28} {path}")
    if not r.ok:
        print(f"         -> {r.text[:200]}")
    return r


def main() -> None:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {get_token()}", "accept": "application/json"})

    print(f"Smoke test - categoría {CATEGORY}\n")
    results = {}

    results["me"] = probe(s, "Usuario del token", "/users/me")
    results["category"] = probe(s, "Detalle de categoría", f"/categories/{CATEGORY}")
    results["search"] = probe(s, "Búsqueda por categoría", "/sites/MLA/search", {"category": CATEGORY, "limit": 5})
    results["highlights"] = probe(s, "Más vendidos (highlights)", f"/highlights/MLA/category/{CATEGORY}")

    # Si highlights funcionó, probamos multiget con los ítems que devolvió
    item_ids = []
    if results["highlights"].ok:
        content = results["highlights"].json().get("content", [])
        item_ids = [c["id"] for c in content if c.get("type") == "ITEM"][:20]
        product_ids = [c["id"] for c in content if c.get("type") == "PRODUCT"][:1]
        print(f"         highlights: {len(content)} resultados ({len(item_ids)} ítems, resto productos de catálogo)")
        if product_ids:
            results["product_items"] = probe(s, "Publicaciones de un producto", f"/products/{product_ids[0]}/items")

    if item_ids:
        results["multiget"] = probe(s, "Multiget de ítems", "/items", {"ids": ",".join(item_ids)})

    failed = [k for k, r in results.items() if not r.ok and k != "search"]
    print("\nResumen:", "todo OK" if not failed else f"fallaron {failed}")
    print("(search puede fallar sin que sea bloqueante: tenemos highlights como alternativa)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
