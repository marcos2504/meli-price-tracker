"""
Fase 0 - Paso 1: obtener el primer access/refresh token de MercadoLibre.

Uso:
    1. Completar .env con ML_CLIENT_ID, ML_CLIENT_SECRET y ML_REDIRECT_URI
    2. python scripts/oauth_bootstrap.py
    3. Abrir la URL que imprime, autorizar, y copiar el valor de `code=` de la URL final
    4. Pegarlo cuando lo pida -> guarda los tokens en .tokens.json (ignorado por git)
"""

import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv

load_dotenv()

CLIENT_ID = os.environ["ML_CLIENT_ID"]
CLIENT_SECRET = os.environ["ML_CLIENT_SECRET"]
REDIRECT_URI = os.environ["ML_REDIRECT_URI"]

AUTH_URL = "https://auth.mercadolibre.com.ar/authorization"
TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


def main() -> None:
    params = {"response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI}
    print("\n1) Abrí esta URL y autorizá la app:\n")
    print(f"   {AUTH_URL}?{urlencode(params)}\n")
    print("2) Te va a redirigir a algo como https://www.google.com/?code=TG-xxxx")
    code = input("   Pegá acá el valor de code: ").strip()

    resp = requests.post(
        TOKEN_URL,
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "redirect_uri": REDIRECT_URI,
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise SystemExit(f"Error {resp.status_code}: {resp.text}")

    tokens = resp.json()
    tokens["expires_at"] = (
        datetime.now(timezone.utc) + timedelta(seconds=tokens["expires_in"])
    ).isoformat()

    with open(".tokens.json", "w") as f:
        json.dump(tokens, f, indent=2)

    print("\nOK - tokens guardados en .tokens.json")
    print(f"   user_id:    {tokens.get('user_id')}")
    print(f"   expira:     {tokens['expires_at']}")
    print(f"   refresh:    {'sí' if tokens.get('refresh_token') else 'NO (revisar scopes offline_access)'}")


if __name__ == "__main__":
    main()
