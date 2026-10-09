import altair as alt
import streamlit as st

import queries as q
from data import load
from fmt import GREEN, PCT_AXIS, RED, ars, pct

st.title("Movimientos de la semana")

overview = load(q.OVERVIEW)
if overview.empty:
    st.info("Todavía no hay datos en gold. El pipeline los carga una vez por día.")
    st.stop()

o = overview.iloc[0]
st.caption(f"Datos al {o.as_of_date:%d/%m/%Y}")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Productos seguidos", int(o.products), border=True)
c2.metric("Publicaciones activas", int(o.listings), border=True)
c3.metric("Vendedores", int(o.sellers), border=True)
c4.metric("Días de historia", int(o.days_of_history), border=True)

movers = load(q.MOVERS)
if movers.empty:
    st.stop()

days = int(movers.days_compared.max())
if days >= 7:
    base = "hace una semana"
else:
    base = f"el primer día disponible ({days} días atrás: todavía no hay una semana)"
st.markdown(
    f"Se compara el **precio más barato** de cada producto, entre todos sus vendedores, contra {base}. "
    "Si sube una publicación que no era la más barata, el producto no cambia: esos movimientos "
    "están en *Cambios de precio*."
)


def movers_column(title: str, rows, empty: str) -> None:
    st.subheader(title)
    if rows.empty:
        with st.container(border=True):
            st.caption(empty)
        return
    for row in rows.itertuples():
        st.metric(
            row.display_name,
            ars(row.current_price),
            delta=f"{pct(row.change_pct)} ({ars(row.change_amount)})",
            delta_color="inverse",  # una baja de precio es buena noticia: verde
            border=True,
        )


drops, rises = st.columns(2)
with drops:
    movers_column(
        "Mayores bajas",
        movers[movers.change_pct < 0].nsmallest(5, "change_pct"),
        "Ningún producto bajó su precio más barato en este período.",
    )
with rises:
    movers_column(
        "Mayores subas",
        movers[movers.change_pct > 0].nlargest(5, "change_pct"),
        "Ningún producto subió su precio más barato en este período.",
    )

st.subheader("Variación del precio más barato")
changed = movers[movers.change_pct.round(1) != 0].copy()
unchanged = len(movers) - len(changed)
if changed.empty:
    st.caption("Ningún producto cambió su precio más barato en este período.")
    st.stop()

changed["variacion"] = changed.change_pct.map(pct)
changed["antes"] = changed.base_price.map(ars)
changed["ahora"] = changed.current_price.map(ars)
chart = (
    alt.Chart(changed)
    .mark_bar(cornerRadius=3)
    .encode(
        x=alt.X("change_pct:Q", title=None, axis=PCT_AXIS),
        y=alt.Y("display_name:N", sort="x", title=None, axis=alt.Axis(labelLimit=360)),
        color=alt.condition(alt.datum.change_pct < 0, alt.value(GREEN), alt.value(RED)),
        tooltip=[
            alt.Tooltip("display_name:N", title="Producto"),
            alt.Tooltip("antes:N", title="Antes"),
            alt.Tooltip("ahora:N", title="Ahora"),
            alt.Tooltip("variacion:N", title="Variación"),
        ],
    )
    .properties(height=60 + 34 * len(changed))
)
st.altair_chart(chart, width="stretch")
if unchanged:
    otros = "El otro producto mantiene" if unchanged == 1 else f"Los otros {unchanged} productos mantienen"
    st.caption(f"{otros} su precio más barato.")
