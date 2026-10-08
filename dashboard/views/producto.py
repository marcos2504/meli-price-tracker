import altair as alt
import pandas as pd
import streamlit as st

import queries as q
from data import load
from fmt import (
    ACCENT,
    CONDITION,
    GREEN,
    LINK_COLUMN,
    MONEY_AXIS,
    ars,
    day_axis,
    delta_color,
    listing_url,
    pct,
    with_day,
)

st.title("Evolución por producto")

products = load(q.PRODUCTS)
if products.empty:
    st.info("Todavía no hay productos con historia de precios.")
    st.stop()

names = dict(zip(products.product_id, products.display_name, strict=True))
searches = list(dict.fromkeys(products.search))

# El producto elegido queda en la URL (?product=MLA...), así se puede compartir el link
requested = st.query_params.get("product")
default_search = (
    products.loc[products.product_id == requested, "search"].iloc[0] if requested in names else searches[0]
)

left, right = st.columns([1, 2])
search = left.selectbox("Búsqueda", searches, index=searches.index(default_search))
options = products.loc[products.search == search, "product_id"].tolist()
product_id = right.selectbox(
    "Producto",
    options,
    index=options.index(requested) if requested in options else 0,
    format_func=names.get,
)
st.query_params["product"] = product_id

history = load(q.PRODUCT_HISTORY, product_id=product_id)
last = history.iloc[-1]
change = last.min_price_change_pct

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Precio más barato",
    ars(last.min_price),
    delta=pct(change) if pd.notna(change) else None,
    delta_color=delta_color(change),
    help="Variación respecto del día anterior",
    border=True,
)
c2.metric("Mediana", ars(last.median_price), border=True)
c3.metric("Vendedores", int(last.sellers), border=True)
c4.metric(
    "Brecha con el 2.º más barato",
    pct(last.price_gap_pct, signed=False),
    help="Cuánto más caro es el segundo vendedor. Una brecha grande indica una oferta puntual.",
    border=True,
)

history, days = with_day(history, "snapshot_date")
long = history.melt(
    id_vars=["dia", "max_price", "sellers"],
    value_vars=["min_price", "median_price"],
    var_name="serie",
    value_name="precio",
)
long["serie"] = long.serie.map({"min_price": "Más barato", "median_price": "Mediana"})
long["precio_txt"] = long.precio.map(ars)
long["max_txt"] = long.max_price.map(ars)

chart = (
    alt.Chart(long)
    .mark_line(point=alt.OverlayMarkDef(size=60), strokeWidth=2.5)
    .encode(
        x=day_axis(days),
        y=alt.Y("precio:Q", title=None, axis=MONEY_AXIS, scale=alt.Scale(zero=False, nice=True)),
        color=alt.Color(
            "serie:N",
            title=None,
            sort=["Más barato", "Mediana"],
            scale=alt.Scale(domain=["Más barato", "Mediana"], range=[GREEN, ACCENT]),
            legend=alt.Legend(orient="top"),
        ),
        tooltip=[
            alt.Tooltip("dia:O", title="Día"),
            alt.Tooltip("serie:N", title="Serie"),
            alt.Tooltip("precio_txt:N", title="Precio"),
            alt.Tooltip("max_txt:N", title="Más caro"),
            alt.Tooltip("sellers:Q", title="Vendedores"),
        ],
    )
    .properties(height=340)
)
st.altair_chart(chart, width="stretch")
st.caption(
    f"El precio más caro ({ars(last.max_price)} hoy) no se grafica: suele ser un vendedor fuera de "
    "mercado y aplasta las otras dos líneas. Está en el detalle de cada punto."
)

st.subheader(f"Publicaciones del {last.snapshot_date:%d/%m/%Y}")
st.caption(f"[Ver el producto en MercadoLibre](https://www.mercadolibre.com.ar/p/{product_id})")
listings = load(q.PRODUCT_LISTINGS, product_id=product_id)
table = pd.DataFrame(
    {
        "Precio": listings.price.map(ars),
        "Precio original": listings.original_price.map(ars),
        "Descuento": listings.discount_pct.map(lambda v: pct(v, signed=False)),
        "Condición": listings.condition.map(lambda c: CONDITION.get(c, c or "—")),
        "Envío gratis": listings.free_shipping.fillna(False).astype(bool),
        "Full": listings.is_full.fillna(False).astype(bool),
        "Tienda oficial": listings.is_official_store.fillna(False).astype(bool),
        "Ubicación": [
            ", ".join(p for p in (city, state) if isinstance(p, str) and p) or "—"
            for city, state in zip(listings.seller_city, listings.seller_state, strict=True)
        ],
        "Publicación": listings.item_id.map(listing_url),
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
