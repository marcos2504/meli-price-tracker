"""
Dashboard de MeLi Price Tracker.

Correr localmente desde la raíz del repo (así toma el tema de .streamlit/config.toml):
    streamlit run dashboard/app.py
"""

import streamlit as st

st.set_page_config(page_title="MeLi Price Tracker", page_icon=":material/sell:", layout="wide")

# Menos aire arriba de la página y un ancho máximo cómodo de leer en pantallas grandes
st.html(
    """
    <style>
      [data-testid="stMainBlockContainer"] { padding-top: 2.5rem; max-width: 1200px; }
      [data-testid="stMetricValue"] { font-size: 1.9rem; }
    </style>
    """
)

pages = [
    st.Page("views/resumen.py", title="Resumen", icon=":material/trending_down:", default=True),
    st.Page("views/producto.py", title="Por producto", icon=":material/show_chart:", url_path="producto"),
    st.Page("views/cambios.py", title="Cambios de precio", icon=":material/swap_vert:", url_path="cambios"),
    st.Page("views/salud.py", title="Salud del pipeline", icon=":material/monitor_heart:", url_path="salud"),
]

nav = st.navigation(pages)

with st.sidebar:
    st.markdown("**MeLi Price Tracker**")
    st.caption(
        "Precios de MercadoLibre Argentina, actualizados todos los días por un pipeline en "
        "GitHub Actions → PostgreSQL → dbt."
    )
    st.caption("[Código en GitHub](https://github.com/marcos2504/meli-price-tracker)")

nav.run()
