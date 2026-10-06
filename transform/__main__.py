"""
Ejecuta dbt con la conexión tomada de DATABASE_URL.

    python -m transform build                  modelos + tests
    python -m transform build --full-refresh   reconstruye todo desde bronze
    python -m transform test
    python -m transform docs generate

dbt-postgres no acepta una URL de conexión, así que este wrapper la descompone en las variables
de entorno que lee dbt/profiles.yml. Así hay un único lugar donde configurar la base.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DBT_DIR = ROOT / "dbt"


def pg_env_from_url(url: str) -> dict[str, str]:
    parts = urlsplit(url)
    if parts.scheme not in ("postgres", "postgresql"):
        raise ValueError("DATABASE_URL tiene que empezar con postgresql://")
    query = parse_qs(parts.query)
    return {
        "PGHOST": parts.hostname or "",
        "PGPORT": str(parts.port or 5432),
        "PGUSER": unquote(parts.username or ""),
        "DBT_ENV_SECRET_PGPASSWORD": unquote(parts.password or ""),
        "PGDATABASE": parts.path.lstrip("/") or "postgres",
        "PGSSLMODE": query.get("sslmode", ["require"])[0],
    }


def main(argv: list[str]) -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL en el archivo .env", file=sys.stderr)
        return 1
    os.environ.update(pg_env_from_url(url))

    from dbt.cli.main import dbtRunner

    args = argv or ["build"]
    result = dbtRunner().invoke([*args, "--project-dir", str(DBT_DIR), "--profiles-dir", str(DBT_DIR)])
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
