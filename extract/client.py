"""
Cliente HTTP de la API de MercadoLibre.

- Reintenta ante 429, 5xx y errores de red, con backoff exponencial y jitter.
  Si la respuesta trae Retry-After, respeta ese tiempo.
- Ante un 401 renueva el token una vez y repite el request.
- Un 404 no es un error del pipeline (por ejemplo, un producto sin vendedores hoy):
  se devuelve como respuesta normal y decide quien llama.
- Lleva métricas de cada corrida (requests, reintentos, 429) para ops.pipeline_runs.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import requests

from extract.auth import TokenManager

log = logging.getLogger(__name__)

RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(RuntimeError):
    def __init__(self, status: int, path: str, body: str):
        super().__init__(f"{status} en {path}: {body[:200]}")
        self.status = status
        self.path = path


@dataclass
class ApiResponse:
    """Respuesta de la API con la metadata que necesita la capa bronze."""

    path: str
    params: dict[str, Any]
    status: int
    data: Any
    fetched_at: datetime

    @property
    def ok(self) -> bool:
        return self.status == 200


@dataclass
class ClientStats:
    requests: int = 0
    retries: int = 0
    rate_limited: int = 0
    errors: dict[int, int] = field(default_factory=dict)


class MeliClient:
    def __init__(
        self,
        tokens: TokenManager,
        base_url: str = "https://api.mercadolibre.com",
        session: requests.Session | None = None,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        timeout: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.tokens = tokens
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.timeout = timeout
        self.sleep = sleep
        self.stats = ClientStats()

    def get(self, path: str, params: dict[str, Any] | None = None) -> ApiResponse:
        params = dict(params or {})
        refreshed = False
        attempt = 0

        while True:
            self.stats.requests += 1
            try:
                resp = self.session.get(
                    f"{self.base_url}{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {self.tokens.get_access_token()}",
                        "accept": "application/json",
                    },
                    timeout=self.timeout,
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                if attempt >= self.max_retries:
                    raise
                attempt += 1
                self._wait(attempt, None, f"error de red ({exc.__class__.__name__})", path)
                continue

            if resp.status_code == 401 and not refreshed:
                log.info("401 en %s: renuevo el token y reintento", path)
                self.tokens.refresh()
                refreshed = True
                continue

            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                if resp.status_code == 429:
                    self.stats.rate_limited += 1
                attempt += 1
                self._wait(attempt, resp.headers.get("Retry-After"), str(resp.status_code), path)
                continue

            if resp.status_code not in (200, 404):
                self.stats.errors[resp.status_code] = self.stats.errors.get(resp.status_code, 0) + 1
                raise ApiError(resp.status_code, path, resp.text)

            return ApiResponse(
                path=path,
                params=params,
                status=resp.status_code,
                data=_json_or_text(resp),
                fetched_at=datetime.now(UTC),
            )

    def _wait(self, attempt: int, retry_after: str | None, reason: str, path: str) -> None:
        self.stats.retries += 1
        delay = self._delay(attempt, retry_after)
        log.warning("%s en %s; reintento %d/%d en %.1fs", reason, path, attempt, self.max_retries, delay)
        self.sleep(delay)

    def _delay(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.backoff_max)
            except ValueError:
                pass  # Retry-After con formato de fecha: usamos el backoff normal
        exp = self.backoff_base * 2 ** (attempt - 1)
        return min(exp + random.uniform(0, self.backoff_base), self.backoff_max)


def _json_or_text(resp: requests.Response) -> Any:
    try:
        return resp.json()
    except ValueError:
        return resp.text
