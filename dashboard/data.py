"""
Acceso a la base desde el dashboard.

- Usa un usuario de solo lectura (db/manual/dashboard_reader.sql), nunca el dueño de la base.
- Cachea cada consulta una hora: los datos cambian una vez por día, y así una visita no despierta
  a Neon por cada clic.
- Abre una conexión por consulta en lugar de mantener una abierta: Neon suspende la base cuando
  no se usa y una conexión guardada quedaría rota.
"""

from __future__ import annotations

import datetime as dt
import os

import pandas as pd
import psycopg
import streamlit as st
from psycopg.types.numeric import FloatLoader

try:  # localmente toma DASHBOARD_DATABASE_URL del .env de la raíz del repo, si está python-dotenv
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

URL_KEY = "DASHBOARD_DATABASE_URL"


class MissingDatabaseUrl(RuntimeError):
    pass


def _database_url() -> str:
    # Variable de entorno (local o CI) o secrets de Streamlit (Community Cloud)
    url = os.environ.get(URL_KEY)
    if not url:
        try:
            url = st.secrets[URL_KEY]
        except Exception:  # no hay secrets.toml o falta la clave
            url = None
    if not url:
        raise MissingDatabaseUrl(URL_KEY)
    return url


@st.cache_data(ttl="1h", show_spinner="Consultando la base…")
def query(sql: str, params: dict | None = None) -> pd.DataFrame:
    # prepare_threshold=None: el endpoint pooled de Neon (PgBouncer) no admite prepared statements
    with psycopg.connect(_database_url(), connect_timeout=20, prepare_threshold=None) as conn:
        conn.read_only = True
        # numeric → float: pandas y los gráficos no trabajan bien con Decimal
        conn.adapters.register_loader("numeric", FloatLoader)
        cur = conn.execute(sql, params)
        df = pd.DataFrame(cur.fetchall(), columns=[c.name for c in cur.description])

    # Las columnas date llegan como objetos de Python: a datetime para poder graficarlas
    for col in df.columns:
        first = df[col].dropna().head(1)
        if not first.empty and type(first.iloc[0]) is dt.date:
            df[col] = pd.to_datetime(df[col])
    return df


def load(sql: str, **params) -> pd.DataFrame:
    """Ejecuta una consulta y, si falla la conexión, muestra un mensaje en lugar del traceback."""
    try:
        return query(sql, params or None)
    except MissingDatabaseUrl:
        st.error(f"Falta configurar `{URL_KEY}` (variable de entorno o secrets de Streamlit).")
    except psycopg.errors.InsufficientPrivilege:
        st.error("El usuario del dashboard no tiene permiso para esta consulta (ver dashboard_reader.sql).")
    except psycopg.OperationalError:
        st.error("No se pudo conectar a la base. Puede estar despertando: recargá en unos segundos.")
    st.stop()
