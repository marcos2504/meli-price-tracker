"""Configuración leída del entorno (y del archivo .env en desarrollo local)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    redirect_uri: str
    site_id: str = "MLA"
    api_base: str = "https://api.mercadolibre.com"
    auth_base: str = "https://auth.mercadolibre.com.ar"
    tokens_path: Path = ROOT / ".tokens.json"
    data_dir: Path = ROOT / "data"
    watchlist_path: Path = ROOT / "watchlist.toml"
    discovery_max_age_days: int = 7


def load_settings() -> Settings:
    missing = [k for k in ("ML_CLIENT_ID", "ML_CLIENT_SECRET", "ML_REDIRECT_URI") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Faltan variables de entorno: {', '.join(missing)}. Revisá el archivo .env")

    return Settings(
        client_id=os.environ["ML_CLIENT_ID"],
        client_secret=os.environ["ML_CLIENT_SECRET"],
        redirect_uri=os.environ["ML_REDIRECT_URI"],
        site_id=os.environ.get("ML_SITE_ID", "MLA"),
        data_dir=Path(os.environ.get("ML_DATA_DIR", ROOT / "data")),
        discovery_max_age_days=int(os.environ.get("ML_DISCOVERY_MAX_AGE_DAYS", "7")),
    )
