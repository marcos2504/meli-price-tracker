"""Dobles de prueba: una sesión HTTP falsa que responde según reglas, sin tocar la red."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from extract.auth import Tokens
from extract.config import Settings


class FakeResponse:
    def __init__(self, status: int, body: Any = None, headers: dict | None = None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}
        self.text = json.dumps(self._body) if not isinstance(self._body, str) else self._body

    def json(self) -> Any:
        if isinstance(self._body, str):
            raise ValueError("no es JSON")
        return self._body


@dataclass
class FakeSession:
    """Cada regla es (método, predicado sobre la URL, respuesta o lista de respuestas en orden)."""

    rules: list[tuple[str, Callable[[str], bool], Any]] = field(default_factory=list)
    calls: list[tuple[str, str, dict]] = field(default_factory=list)

    def add(self, method: str, match: Callable[[str], bool], responses) -> FakeSession:
        if not isinstance(responses, list):
            responses = [responses]
        self.rules.append((method, match, responses))
        return self

    def _handle(self, method: str, url: str, **kwargs):
        self.calls.append((method, url, kwargs))
        for m, match, responses in self.rules:
            if m == method and match(url):
                item = responses.pop(0) if len(responses) > 1 else responses[0]
                if isinstance(item, Exception):
                    raise item
                return item
        return FakeResponse(404, {"message": "sin regla para " + url})

    def get(self, url, **kwargs):
        return self._handle("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._handle("POST", url, **kwargs)


def settings(tmp: Path) -> Settings:
    return Settings(
        client_id="123",
        client_secret="secret",
        redirect_uri="https://www.google.com",
        tokens_path=tmp / ".tokens.json",
        data_dir=tmp / "data",
        watchlist_path=tmp / "watchlist.toml",
    )


def valid_tokens(minutes: int = 60) -> Tokens:
    return Tokens(
        access_token="ACCESS-OLD",
        refresh_token="REFRESH-OLD",
        expires_at=datetime.now(UTC) + timedelta(minutes=minutes),
        user_id=1,
    )


def oauth_body(access: str = "ACCESS-NEW", refresh: str = "REFRESH-NEW") -> dict:
    return {"access_token": access, "refresh_token": refresh, "expires_in": 21600, "user_id": 1}


connection_error = requests.ConnectionError("conexión caída")
