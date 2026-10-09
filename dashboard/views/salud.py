import altair as alt
import pandas as pd
import streamlit as st

import queries as q
from data import load
from fmt import COUNT_AXIS, GRAY, GREEN, RED, STATUS, TRIGGER, day_axis, with_day

TZ = "America/Argentina/Buenos_Aires"

st.title("Salud del pipeline")
st.markdown(
    "Cada ejecución del pipeline (extracción de la API, carga en bronze y `dbt build`): la "
    "**programada**, que corre sola todos los días a las 10:17, y las **manuales** (pruebas o reintentos)."
)

runs = load(q.PIPELINE_RUNS, limit=60)
if runs.empty:
    st.info("Todavía no hay corridas registradas.")
    st.stop()

runs["inicio"] = pd.to_datetime(runs.started_at, utc=True).dt.tz_convert(TZ)
runs["estado"] = runs.status.map(STATUS).fillna(runs.status)
# Las corridas anteriores a la migración 002 no registraban el origen
runs["origen"] = runs.triggered_by.map(TRIGGER).fillna("Sin dato")

ok = runs[runs.status == "success"]
finished = runs[runs.status != "running"]
last_ok = ok.inicio.max() if not ok.empty else None
hours_since = (pd.Timestamp.now(tz=TZ) - last_ok).total_seconds() / 3600 if last_ok is not None else None

c1, c2, c3, c4 = st.columns(4)
c1.metric(
    "Última corrida exitosa",
    f"{last_ok:%d/%m %H:%M}" if last_ok is not None else "—",
    help="Hora de Argentina",
    border=True,
)
c2.metric("Horas desde entonces", f"{hours_since:.0f}" if hours_since is not None else "—", border=True)
c3.metric(
    "Tasa de éxito",
    f"{100 * (finished.status == 'success').mean():.0f} %" if not finished.empty else "—",
    help=f"Sobre las últimas {len(finished)} corridas terminadas",
    border=True,
)
c4.metric(
    "Duración mediana",
    f"{ok.duration_s.median():.0f} s" if not ok.empty else "—",
    help="De las corridas exitosas",
    border=True,
)

if hours_since is not None and hours_since > 26:
    st.warning("Pasó más de un día desde la última corrida exitosa.")

per_day = (
    runs.assign(fecha=runs.inicio.dt.tz_localize(None).dt.normalize())
    .groupby(["fecha", "estado"], as_index=False)
    .size()
)
per_day, chart_days = with_day(per_day, "fecha")
chart = (
    alt.Chart(per_day)
    .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
    .encode(
        x=day_axis(chart_days),
        y=alt.Y("size:Q", title="Corridas", axis=COUNT_AXIS),
        color=alt.Color(
            "estado:N",
            title=None,
            scale=alt.Scale(domain=list(STATUS.values()), range=[GREEN, RED, GRAY]),
            legend=alt.Legend(orient="top"),
        ),
        tooltip=[
            alt.Tooltip("dia:O", title="Día"),
            alt.Tooltip("estado:N", title="Estado"),
            alt.Tooltip("size:Q", title="Corridas"),
        ],
    )
    .properties(height=240)
)
st.altair_chart(chart, width="stretch")

table = pd.DataFrame(
    {
        "Inicio": runs.inicio.dt.strftime("%d/%m/%Y %H:%M"),
        "ID": runs.run_id.str.rsplit("-", n=1).str[-1],
        "Origen": runs.origen,
        "Estado": runs.estado,
        "Duración": runs.duration_s.map(lambda s: "—" if pd.isna(s) else f"{s:.0f} s"),
        "Descubrimiento": runs.discovery.fillna(False).astype(bool),
        "Productos": runs.products.map(lambda v: "—" if pd.isna(v) else f"{v:.0f}"),
        "Publicaciones": runs.listings.map(lambda v: "—" if pd.isna(v) else f"{v:.0f}"),
    }
)
st.dataframe(
    table,
    hide_index=True,
    width="stretch",
    column_config={
        "Inicio": st.column_config.TextColumn("Inicio (hora AR)"),
        "ID": st.column_config.TextColumn("ID", help="Últimos caracteres del run_id"),
        "Descubrimiento": st.column_config.CheckboxColumn(
            "Descubrimiento", help="Si la corrida volvió a buscar los productos de la watchlist"
        ),
    },
)
