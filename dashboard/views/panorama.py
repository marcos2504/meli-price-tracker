"""
Panorama: tablero de gestión y página de inicio del dashboard.

Secciones, de lo general a lo particular:
  a) Encabezado: cuántos productos se siguen, fecha de los datos y días de historia.
  b) Índice de precios (cada producto arranca en 100 el primer día) y los KPIs del día.
  c) Mapa de calor: variación diaria del precio más barato de cada producto.
  d) Una tarjeta por producto con su precio de hoy y la evolución del último mes.
  e) Cómo se vende hoy: envío gratis, Full, tiendas oficiales y descuentos.
  f) Dónde están los vendedores, por provincia.
"""

import altair as alt
import pandas as pd
import streamlit as st

import queries as q
from data import load
from fmt import ACCENT, COUNT_AXIS, DROP, GRAY, MID, RISE, ars, day, day_axis, delta_color, pct, with_day

WINDOW_DAYS = 30  # ventana del mapa de calor y de las tarjetas por producto
HEAT_LIMIT = 5  # % en el que satura el color del mapa de calor
NAME_LIMIT = 34  # largo máximo del nombre en las tarjetas por producto
TOP_STATES = 8


def thousands(value: float) -> str:
    """2481 → 2.481"""
    return f"{int(value):,}".replace(",", ".")


def decimal(value: float) -> str:
    """103.42 → 103,4"""
    return f"{value:.1f}".replace(".", ",")


overview = load(q.OVERVIEW)
index = load(q.PRICE_INDEX)
if overview.empty or index.empty:
    st.info("Todavía no hay datos en gold. El pipeline los carga una vez por día.")
    st.stop()

o = overview.iloc[0]

# --- a) Encabezado --------------------------------------------------------------------------
st.title("Panorama del mercado")
st.caption(
    f"MercadoLibre Argentina · {int(o.products)} productos seguidos · "
    f"datos al {o.as_of_date:%d/%m/%Y} · {int(o.days_of_history)} días de historia"
)

# --- b) Índice de precios y KPIs del día ----------------------------------------------------
hero, kpis = st.columns([2, 1], gap="medium")

with hero, st.container(border=True, key="hero"):
    current = index.price_index.iloc[-1]
    variation = current - 100
    st.metric(
        "Índice de precios",
        decimal(current),
        delta=f"{pct(variation)} desde el {day(index.snapshot_date.iloc[0])}",
        delta_color=delta_color(variation),
        help=(
            "Cada producto arranca en 100 el primer día que se lo siguió, con su precio más barato. "
            "El índice es el promedio entre todos los productos: 105 significa que, en promedio, "
            "el precio más barato subió 5 %."
        ),
    )

    index, index_days = with_day(index, "snapshot_date")
    index["indice_txt"] = index.price_index.map(decimal)
    line = (
        alt.Chart(index)
        .mark_line(color=ACCENT, strokeWidth=2, point=alt.OverlayMarkDef(size=18, color=ACCENT))
        .encode(
            x=day_axis(index_days),
            y=alt.Y(
                "price_index:Q",
                title=None,
                scale=alt.Scale(zero=False),
                axis=alt.Axis(tickCount=4, labelExpr="replace(format(datum.value, '.1f'), '.', ',')"),
            ),
            tooltip=[
                alt.Tooltip("dia:O", title="Día"),
                alt.Tooltip("indice_txt:N", title="Índice"),
                alt.Tooltip("products:Q", title="Productos"),
            ],
        )
    )
    base_rule = alt.Chart(pd.DataFrame({"base": [100]})).mark_rule(color=GRAY).encode(y="base:Q")
    st.altair_chart((base_rule + line).properties(height=190), width="stretch")

with kpis:
    st.metric("Publicaciones activas", thousands(o.listings), border=True)
    st.metric("Vendedores", thousands(o.sellers), border=True)
    summary = load(q.CHANGES_SUMMARY, days=7).iloc[0]
    drops, rises = int(summary.drops), int(summary.rises)
    st.metric(
        "Cambios de precio (7 días)",
        thousands(drops + rises),
        delta=f"{thousands(drops)} bajas · {thousands(rises)} subas",
        delta_color="off",
        delta_arrow="off",
        border=True,
    )

# --- c) Mapa de calor -----------------------------------------------------------------------
grid = load(q.DAILY_GRID, days=WINDOW_DAYS)
if grid.empty:
    st.stop()

st.subheader("Variación diaria del precio más barato")
st.caption(
    "Cada celda es un producto en un día, comparado con el día anterior: azul si bajó, rojo si subió "
    "y gris si no cambió. Los días de ofertas aparecen como columnas azules."
)

grid, grid_days = with_day(grid, "snapshot_date")
# La consulta ya viene ordenada por búsqueda y nombre; el sort estable de with_day lo conserva
grid = grid.sort_values(["search", "display_name", "snapshot_date"])
names = list(dict.fromkeys(grid.display_name))
grid["variacion_txt"] = grid.min_price_change_pct.map(pct)
grid["precio_txt"] = grid.min_price.map(ars)

heatmap = (
    alt.Chart(grid)
    .mark_rect(cornerRadius=2, stroke="#FFFFFF", strokeWidth=2)
    .encode(
        x=alt.X(
            "dia:O",
            sort=grid_days,
            title=None,
            axis=alt.Axis(orient="top", labelAngle=0, labelOverlap="greedy", domain=False, ticks=False),
        ),
        y=alt.Y(
            "display_name:N",
            sort=names,
            title=None,
            axis=alt.Axis(labelLimit=300, domain=False, ticks=False),
        ),
        color=alt.Color(
            "min_price_change_pct:Q",
            title=None,
            scale=alt.Scale(
                type="linear",
                domain=[-HEAT_LIMIT, 0, HEAT_LIMIT],
                range=[DROP, MID, RISE],
                clamp=True,
            ),
            legend=alt.Legend(
                orient="bottom",
                direction="horizontal",
                gradientLength=220,
                labelExpr="(datum.value > 0 ? '+' : '') + datum.value + ' %'",
            ),
        ),
        tooltip=[
            alt.Tooltip("display_name:N", title="Producto"),
            alt.Tooltip("dia:O", title="Día"),
            alt.Tooltip("variacion_txt:N", title="Variación"),
            alt.Tooltip("precio_txt:N", title="Precio más barato"),
        ],
    )
    .properties(height=26 * len(names) + 30)
)
st.altair_chart(heatmap, width="stretch")

with st.expander("Ver los datos del mapa de calor"):
    pivot = (
        grid.drop_duplicates(["display_name", "dia"])
        .pivot(index="display_name", columns="dia", values="min_price_change_pct")
        .reindex(index=names, columns=grid_days)
        .map(pct)
        .rename_axis(index="Producto", columns=None)
    )
    st.dataframe(pivot, width="stretch")

# --- d) Una tarjeta por producto ------------------------------------------------------------
st.subheader("Cada producto, último mes")
for search, group in grid.groupby("search", sort=False):
    st.markdown(f"**{search}**")
    products = [rows for _, rows in group.groupby("product_id", sort=False)]
    for start in range(0, len(products), 4):
        for column, rows in zip(st.columns(4), products[start : start + 4], strict=False):
            name = rows.display_name.iloc[0]
            prices = rows.min_price.tolist()
            change = 100 * (prices[-1] / prices[0] - 1) if prices[0] else None
            column.metric(
                name if len(name) <= NAME_LIMIT else name[: NAME_LIMIT - 1].rstrip() + "…",
                ars(prices[-1]),
                delta=pct(change) if change is not None else None,
                delta_color=delta_color(change),
                help=name,
                chart_data=prices,
                chart_type="line",
                border=True,
            )

# --- e) Cómo se vende hoy -------------------------------------------------------------------
mix = load(q.MARKET_MIX)
if not mix.empty and mix.listings.iloc[0]:
    m = mix.iloc[0]
    st.subheader("Cómo se vende hoy")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Con envío gratis", pct(100 * m.free_shipping_share, signed=False), border=True)
    c2.metric(
        "Con envío Full",
        pct(100 * m.full_share, signed=False),
        help="El stock está en los depósitos de MercadoLibre, que se encarga del envío.",
        border=True,
    )
    c3.metric("De tiendas oficiales", pct(100 * m.official_share, signed=False), border=True)
    c4.metric(
        "Con descuento",
        pct(100 * m.discounted_share, signed=False),
        delta=f"{pct(m.avg_discount, signed=False)} promedio" if pd.notna(m.avg_discount) else None,
        delta_color="off",
        delta_arrow="off",
        border=True,
    )

# --- f) Dónde están los vendedores ----------------------------------------------------------
states = load(q.SELLERS_BY_STATE)
if not states.empty:
    st.subheader("Dónde están los vendedores")
    top = states.head(TOP_STATES)
    rest = int(states.sellers.iloc[TOP_STATES:].sum())
    if rest:
        top = pd.concat([top, pd.DataFrame({"state": ["Otras"], "sellers": [rest]})], ignore_index=True)
    bars = (
        alt.Chart(top)
        .mark_bar(color=ACCENT, cornerRadiusEnd=4)
        .encode(
            x=alt.X("sellers:Q", title=None, axis=COUNT_AXIS),
            y=alt.Y("state:N", sort=top.state.tolist(), title=None, axis=alt.Axis(labelLimit=220)),
            tooltip=[
                alt.Tooltip("state:N", title="Provincia"),
                alt.Tooltip("sellers:Q", title="Vendedores"),
            ],
        )
        .properties(height=40 + 28 * len(top))
    )
    st.altair_chart(bars, width="stretch")

# --- g) Pie ---------------------------------------------------------------------------------
st.caption(f"Día de referencia: {day(o.as_of_date)}. Los datos se actualizan una vez por día.")
