"""
Autenticación OAuth 2.0 contra MercadoLibre.

- `Tokens`: access token, refresh token y vencimiento.
- `TokenStore`: dónde se persisten los tokens: un archivo local o la tabla ops.auth_tokens.
- `TokenManager`: entrega un access token válido y lo renueva solo cuando está por vencer.
  MercadoLibre rota el refresh token en cada uso, así que cada renovación se guarda de inmediato
  y se hace bajo un lock: si dos corridas renuevan a la vez, una invalidaría el token de la otra.
- `bootstrap`: flujo de autorización inicial (una sola vez), con PKCE opcional.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode

import requests

from extract.config import Settings

log = logging.getLogger(__name__)

# Renovar un poco antes del vencimiento real, para no usar un token que expira en pleno request
REFRESH_MARGIN = timedelta(minutes=5)


class AuthError(RuntimeError):
    """El token no se puede obtener ni renovar: hay que volver a correr el bootstrap."""


@dataclass
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: datetime
    user_id: int | None = None

    @classmethod
    def from_oauth_response(cls, data: dict, now: datetime | None = None) -> Tokens:
        now = now or datetime.now(UTC)
        return cls(
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=now + timedelta(seconds=int(data["expires_in"])),
            user_id=data.get("user_id"),
        )

    def is_expiring(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        return now >= self.expires_at - REFRESH_MARGIN

    def to_dict(self) -> dict:
        d = asdict(self)
        d["expires_at"] = self.expires_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> Tokens:
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            expires_at=datetime.fromisoformat(d["expires_at"]),
            user_id=d.get("user_id"),
        )


class TokenStore(Protocol):
    def load(self) -> Tokens | None: ...
    def save(self, tokens: Tokens) -> None: ...
    def lock(self) -> AbstractContextManager[None]:
        """Exclusión mutua durante la renovación del token."""
        ...


class FileTokenStore:
    """Guarda los tokens en un JSON local. Compatible con el archivo que genera el bootstrap de la Fase 0."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> Tokens | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if "expires_at" not in data:
            return None
        return Tokens.from_dict(data)

    def save(self, tokens: Tokens) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(tokens.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(self.path)  # escritura atómica: nunca queda un archivo a medio escribir

    def lock(self) -> AbstractContextManager[None]:
        return nullcontext()  # uso local: una sola corrida a la vez


class PostgresTokenStore:
    """Guarda los tokens en ops.auth_tokens (una sola fila)."""

    def __init__(self, conn):
        self.conn = conn

    def load(self) -> Tokens | None:
        row = self.conn.execute(
            "select access_token, refresh_token, expires_at, user_id from ops.auth_tokens where id = 1"
        ).fetchone()
        if row is None:
            return None
        return Tokens(access_token=row[0], refresh_token=row[1], expires_at=row[2], user_id=row[3])

    def save(self, tokens: Tokens) -> None:
        self.conn.execute(
            """
            insert into ops.auth_tokens (id, access_token, refresh_token, expires_at, user_id, updated_at)
            values (1, %s, %s, %s, %s, now())
            on conflict (id) do update set
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                user_id = excluded.user_id,
                updated_at = now()
            """,
            (tokens.access_token, tokens.refresh_token, tokens.expires_at, tokens.user_id),
        )

    @contextmanager
    def lock(self) -> Iterator[None]:
        # Advisory lock de transacción: otra corrida que quiera renovar espera hasta que esta termine
        with self.conn.transaction():
            self.conn.execute("select pg_advisory_xact_lock(hashtext('meli-price-tracker:oauth'))")
            yield


class TokenManager:
    def __init__(self, settings: Settings, store: TokenStore, session: requests.Session | None = None):
        self.settings = settings
        self.store = store
        self.session = session or requests.Session()
        self._tokens: Tokens | None = None

    def get_access_token(self) -> str:
        if self._tokens is None:
            self._tokens = self.store.load()
        if self._tokens is None:
            raise AuthError("No hay tokens guardados. Corré: python -m extract bootstrap")
        if self._tokens.is_expiring():
            self.refresh()
        return self._tokens.access_token

    def refresh(self) -> Tokens:
        """Canjea el refresh token por uno nuevo. Se llama solo o ante un 401 del cliente."""
        with self.store.lock():
            latest = self.store.load()
            if (
                latest is not None
                and self._tokens is not None
                and latest.refresh_token != self._tokens.refresh_token
                and not latest.is_expiring()
            ):
                # Otra corrida ya renovó mientras esperábamos el lock: usamos su token
                log.info("Token renovado por otra corrida; lo reuso")
                self._tokens = latest
                return latest
            return self._refresh_with(latest or self._tokens)

    def _refresh_with(self, current: Tokens | None) -> Tokens:
        if current is None:
            raise AuthError("No hay refresh token. Corré: python -m extract bootstrap")

        resp = self.session.post(
            f"{self.settings.api_base}/oauth/token",
            headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
                "refresh_token": current.refresh_token,
            },
            timeout=30,
        )
        if resp.status_code != 200:
            raise AuthError(
                f"No se pudo renovar el token ({resp.status_code}): {resp.text[:200]}. "
                "Si el refresh token venció o fue revocado, corré: python -m extract bootstrap"
            )

        self._tokens = Tokens.from_oauth_response(resp.json())
        self.store.save(self._tokens)  # guardar ya: el refresh token anterior dejó de servir
        log.info("Token renovado; vence %s", self._tokens.expires_at.isoformat())
        return self._tokens


# --- PKCE --------------------------------------------------------------------------------


def generate_code_verifier() -> str:
    """Texto aleatorio de 43 a 128 caracteres (RFC 7636)."""
    return secrets.token_urlsafe(64)[:96]


def code_challenge_s256(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# --- Bootstrap ---------------------------------------------------------------------------


def authorization_url(settings: Settings, code_challenge: str | None = None) -> str:
    params = {"response_type": "code", "client_id": settings.client_id, "redirect_uri": settings.redirect_uri}
    if code_challenge:
        params |= {"code_challenge": code_challenge, "code_challenge_method": "S256"}
    return f"{settings.auth_base}/authorization?{urlencode(params)}"


def exchange_code(
    settings: Settings, code: str, code_verifier: str | None = None, session: requests.Session | None = None
) -> Tokens:
    data = {
        "grant_type": "authorization_code",
        "client_id": settings.client_id,
        "client_secret": settings.client_secret,
        "code": code,
        "redirect_uri": settings.redirect_uri,
    }
    if code_verifier:
        data["code_verifier"] = code_verifier

    resp = (session or requests).post(
        f"{settings.api_base}/oauth/token",
        headers={"accept": "application/json", "content-type": "application/x-www-form-urlencoded"},
        data=data,
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError(f"No se pudo canjear el code ({resp.status_code}): {resp.text[:300]}")
    return Tokens.from_oauth_response(resp.json())


def bootstrap(
    settings: Settings, store: TokenStore, use_pkce: bool, ask: Callable[[str], str] = input
) -> Tokens:
    """Flujo interactivo: muestra la URL de autorización, pide el code y guarda los tokens."""
    verifier = generate_code_verifier() if use_pkce else None
    challenge = code_challenge_s256(verifier) if verifier else None

    print("\n1) Abrí esta URL y autorizá la app:\n")
    print(f"   {authorization_url(settings, challenge)}\n")
    print(f"2) Te va a redirigir a {settings.redirect_uri}/?code=TG-...")
    code = ask("   Pegá acá el valor de code: ").strip()

    tokens = exchange_code(settings, code, verifier)
    store.save(tokens)
    return tokens
