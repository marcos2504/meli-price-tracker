"""
Formato único para todo el dashboard: números argentinos ($ 1.234.567 y -4,2 %), fechas por día y
textos en castellano. Los KPIs, las tablas y los ejes de los gráficos usan estas mismas funciones.
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

GREEN, RED, GRAY, ACCENT = "#16A34A", "#DC2626", "#9CA3AF", "#4F46E5"

CONDITION = {"new": "Nuevo", "used": "Usado", "refurbished": "Reacondicionado"}
STATUS = {"success": "Exitosa", "failed": "Fallida", "running": "En curso"}
TRIGGER = {"schedule": "Programada", "workflow_dispatch": "Manual", "local": "Local"}


def ars(value: float | None) -> str:
    """Precio en pesos, sin centavos: 1490674.71 → $ 1.490.675; -2170.84 → -$ 2.171"""
    if value is None or pd.isna(value):
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}$ " + f"{abs(value):,.0f}".replace(",", ".")


def pct(value: float | None, signed: bool = True) -> str:
    """Porcentaje con coma decimal: -4.21 → -4,2 %; 0 → 0,0 %"""
    if value is None or pd.isna(value):
        return "—"
    if round(value, 1) == 0:
        return "0,0 %"
    return f"{value:{'+' if signed else ''}.1f} %".replace(".", ",")


def day(value) -> str:
    return "—" if value is None or pd.isna(value) else f"{pd.Timestamp(value):%d/%m}"


def delta_color(value: float | None) -> str:
    """Para st.metric con precios: bajar es bueno (verde), subir es malo (rojo), 0 es neutro."""
    if value is None or pd.isna(value) or round(value, 1) == 0:
        return "off"
    return "inverse"


def listing_url(item_id: str) -> str:
    """MLA3875311476 → https://articulo.mercadolibre.com.ar/MLA-3875311476"""
    return f"https://articulo.mercadolibre.com.ar/{item_id[:3]}-{item_id[3:]}"


# Columna con link a la publicación; muestra solo el ID (MLA-3875311476)
LINK_COLUMN = st.column_config.LinkColumn("Publicación", display_text=r"articulo\.mercadolibre\.com\.ar/(.*)")


# --- Gráficos -------------------------------------------------------------------------------
# Vega formatea los números en inglés (3,200,000). labelExpr los pasa al formato argentino.

MONEY_AXIS = alt.Axis(
    labelExpr="'$ ' + replace(format(datum.value, ',.0f'), regexp(',', 'g'), '.')",
    tickCount=5,
    grid=True,
)
PCT_AXIS = alt.Axis(labelExpr="replace(format(datum.value, '.1f'), '.', ',') + ' %'", tickCount=5)
COUNT_AXIS = alt.Axis(format="d", tickMinStep=1)


def day_axis(days: list[str]) -> alt.X:
    """Eje x de un valor por día. Ordinal y no temporal: con pocos días, un eje temporal pone una
    marca por hora (06/10 06/10 06/10...) y deja las barras finitas."""
    return alt.X(
        "dia:O",
        sort=days,
        title=None,
        axis=alt.Axis(labelAngle=0, labelOverlap="greedy"),
    )


def with_day(df: pd.DataFrame, column: str) -> tuple[pd.DataFrame, list[str]]:
    """Agrega la columna 'dia' (dd/mm) y devuelve los días en orden cronológico."""
    df = df.sort_values(column).assign(dia=lambda d: d[column].map(day))
    return df, list(dict.fromkeys(df["dia"]))
