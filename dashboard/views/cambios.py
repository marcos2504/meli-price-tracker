import altair as alt
import pandas as pd
import streamlit as st

import queries as q
from data import load
from fmt import COUNT_AXIS, GREEN, LINK_COLUMN, RED, ars, day, day_axis, listing_url, pct, with_day

st.title("Cambios de precio")
st.markdown(
    "Cada vez que **una publicación** cambió de precio, según el historial SCD2. A diferencia del "
    "Resumen, que mira solo el precio más barato de cada producto, acá aparece cualquier vendedor. "
    "Las publicaciones que aparecen o desaparecen no cuentan como cambio."
)

days = st.segmented_control("Período", [7, 14, 30, 90], default=30, format_func=lambda d: f"{d} días")
changes = load(q.PRICE_CHANGES, days=days or 30)

if changes.empty:
    st.info("No hubo cambios de precio en este período.")
    st.stop()

searches = list(dict.fromkeys(changes.search.dropna()))
selected = st.multiselect("Búsquedas", searches, default=searches)
changes = changes[changes.search.isin(selected)]
if changes.empty:
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Bajas", int((changes.change_pct < 0).sum()), border=True)
c2.metric("Subas", int((changes.change_pct > 0).sum()), border=True)
c3.metric("Variación mediana", pct(changes.change_pct.median()), border=True)

per_day = (
    changes.assign(tipo=changes.change_pct.lt(0).map({True: "Baja", False: "Suba"}))
    .groupby(["change_date", "tipo"], as_index=False)
    .size()
)
per_day, chart_days = with_day(per_day, "change_date")
chart = (
    alt.Chart(per_day)
    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
    .encode(
        x=day_axis(chart_days),
        xOffset=alt.XOffset("tipo:N", sort=["Baja", "Suba"]),
        y=alt.Y("size:Q", title="Cambios", axis=COUNT_AXIS),
        color=alt.Color(
            "tipo:N",
            title=None,
            scale=alt.Scale(domain=["Baja", "Suba"], range=[GREEN, RED]),
            legend=alt.Legend(orient="top"),
        ),
        tooltip=[
            alt.Tooltip("dia:O", title="Día"),
            alt.Tooltip("tipo:N", title="Tipo"),
            alt.Tooltip("size:Q", title="Cambios"),
        ],
    )
    .properties(height=260)
)
st.altair_chart(chart, width="stretch")

table = pd.DataFrame(
    {
        "Fecha": changes.change_date.map(day),
        "Producto": changes.product_name,
        "Antes": changes.previous_price.map(ars),
        "Ahora": changes.new_price.map(ars),
        "Diferencia": changes.change_amount.map(ars),
        "Variación": changes.change_pct.map(pct),
        "Publicación": changes.item_id.map(listing_url),
    }
)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Publicación": LINK_COLUMN,
    },
)
