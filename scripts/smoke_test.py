"""
Fase 0: probar los endpoints que usa el pipeline.
 
Toma el access token de la variable ML_ACCESS_TOKEN (GitHub Actions) o de .tokens.json (local).
Correrlo local y en Actions: si local da 200 y Actions 403, el bloqueo es por IP del runner.
 
Endpoints que usa el pipeline:
    /products/search            descubrimiento de productos de catálogo
    /products/{id}/items        publicaciones y precios de un producto
"""
 
import json
import os
import sys
 
import requests
 
BASE = "https://api.mercadolibre.com"
QUERY = os.environ.get("ML_QUERY", "iphone 15")
DOMAIN = os.environ.get("ML_DOMAIN", "MLA-CELLPHONES")
KNOWN_PRODUCT = os.environ.get("ML_PRODUCT", "MLA1027172677")  # iPhone 15 128 GB Negro
 
 
def get_token() -> str:
    token = os.environ.get("ML_ACCESS_TOKEN")
    if token:
        return token
    with open(".tokens.json") as f:
        return json.load(f)["access_token"]
 
 
def probe(s: requests.Session, name: str, path: str, params: dict | None = None):
    r = s.get(f"{BASE}{path}", params=params, timeout=30)
    tag = "OK  " if r.ok else "FAIL"
    print(f"[{tag}] {r.status_code}  {name:<30} {path}")
    if not r.ok:
        print(f"         -> {r.text[:200]}")
    return r
 
 
def main() -> None:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {get_token()}", "accept": "application/json"})
    results = {}
 
    results["token"] = probe(s, "Usuario del token", "/users/me").ok
 
    r = probe(
        s,
        "Búsqueda en catálogo",
        "/products/search",
        {"status": "active", "site_id": "MLA", "q": QUERY, "domain_id": DOMAIN, "limit": 5},
    )
    results["discovery"] = r.ok
    if r.ok:
        total = (r.json().get("paging") or {}).get("total")
        print(f"         '{QUERY}' en {DOMAIN}: {total} productos")
 
    r = probe(s, "Publicaciones de un producto", f"/products/{KNOWN_PRODUCT}/items")
    results["prices"] = r.ok
    if r.ok:
        listings = r.json().get("results", [])
        prices = sorted(x["price"] for x in listings if x.get("price") is not None)
        print(f"         {len(listings)} publicaciones; precios: {prices}")
    elif r.status_code == 404:
        print("         404 = el producto no tiene vendedores hoy; probá con otro ML_PRODUCT")
 
    failed = [k for k, ok in results.items() if not ok]
    print("\nResumen:", "todo OK, la extracción es viable" if not failed else f"fallaron {failed}")
    sys.exit(1 if failed else 0)
 
 
if __name__ == "__main__":
    main()