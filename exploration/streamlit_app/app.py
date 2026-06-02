import json
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"
GEOJSON_PETROLEO_PATH = PROCESSED_DIR / "cuencas_sedimentarias_sesco_petroleo.geojson"
GEOJSON_GAS_PATH = PROCESSED_DIR / "cuencas_sedimentarias_sesco_gas.geojson"
MATCH_REPORT_PATH = PROCESSED_DIR / "cuencas_sesco_match_report.csv"
GEO_COVERAGE_SUMMARY_PATH = PROCESSED_DIR / "cuencas_sesco_geo_coverage_summary.csv"


st.set_page_config(
    page_title="Monitor de Producción Hidrocarburífera Argentina — SESCO",
    page_icon="🛢️",
    layout="wide",
)

st.title("Monitor de Producción Hidrocarburífera Argentina — SESCO")
st.markdown(
    """
    MVP inicial para explorar la producción mensual de petróleo y gas con datos
    procesados localmente desde SESCO (datos.gob.ar), sin backend ni base de datos.
    """
)


@st.cache_data
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    base_path = Path(__file__).resolve().parents[1] / "data" / "processed"

    df = pd.read_csv(base_path / "sesco_produccion_model_clean.csv")
    latest_df = pd.read_csv(base_path / "sesco_latest_periods_by_view.csv")
    config_df = pd.read_csv(base_path / "sesco_dashboard_config.csv")

    df["periodo_dt"] = pd.to_datetime(df["periodo_dt"], errors="coerce")
    df = df.sort_values("periodo_dt").reset_index(drop=True)

    latest_df["latest_valid_period"] = pd.to_datetime(
        latest_df["latest_valid_period"] + "-01", errors="coerce"
    )
    config_df["periodo_min"] = pd.to_datetime(config_df["periodo_min"] + "-01", errors="coerce")
    config_df["latest_valid_period"] = pd.to_datetime(
        config_df["latest_valid_period"] + "-01", errors="coerce"
    )

    return df, latest_df, config_df


@st.cache_data
def load_geojson_cuencas(path_str: str) -> Optional[gpd.GeoDataFrame]:
    path = Path(path_str)
    if not path.is_file():
        return None
    return gpd.read_file(path)


@st.cache_data
def load_match_report() -> Optional[pd.DataFrame]:
    if not MATCH_REPORT_PATH.is_file():
        return None
    return pd.read_csv(MATCH_REPORT_PATH)


@st.cache_data
def load_geo_coverage_summary() -> Optional[pd.DataFrame]:
    if not GEO_COVERAGE_SUMMARY_PATH.is_file():
        return None
    return pd.read_csv(GEO_COVERAGE_SUMMARY_PATH)


def build_cuenca_choropleth(
    gdf_map: gpd.GeoDataFrame,
    match_df: Optional[pd.DataFrame],
    titulo: str,
) -> go.Figure:
    if "produccion" not in gdf_map.columns:
        raise ValueError("El GeoJSON no incluye la columna 'produccion'.")

    plot_df = gdf_map.copy()
    plot_df["map_id"] = plot_df.index.astype(str)
    plot_df["produccion_txt"] = plot_df["produccion"].apply(
        lambda x: f"{x:,.2f}" if pd.notna(x) else "N/D"
    )

    if match_df is not None and "cuenca_geo" in match_df.columns:
        eq_map = (
            match_df.dropna(subset=["cuenca_geo", "equivalencia_usada"])
            .drop_duplicates("cuenca_geo")
            .set_index("cuenca_geo")["equivalencia_usada"]
        )
        plot_df["equivalencia_usada"] = plot_df["cuenca"].map(eq_map)
    elif "equivalencia_usada" in plot_df.columns:
        plot_df["equivalencia_usada"] = plot_df["equivalencia_usada"]
    else:
        plot_df["equivalencia_usada"] = pd.NA
    plot_df["equivalencia_usada"] = plot_df["equivalencia_usada"].fillna("—")
    plot_df["match_status"] = plot_df["match_status"].fillna("—")

    geojson = json.loads(plot_df[["map_id", "geometry"]].to_json())
    cols_plot = [
        "map_id",
        "cuenca",
        "producto",
        "periodo_str",
        "produccion",
        "produccion_txt",
        "tipo",
        "ubicacion",
        "origen_geo",
        "match_status",
        "equivalencia_usada",
    ]
    plot_attrs = plot_df[[c for c in cols_plot if c in plot_df.columns]].copy()

    fig = px.choropleth_map(
        plot_attrs,
        geojson=geojson,
        locations="map_id",
        featureidkey="properties.map_id",
        color="produccion",
        color_continuous_scale="YlOrRd",
        map_style="open-street-map",
        zoom=3.6,
        center={"lat": -38.5, "lon": -64.0},
        opacity=0.72,
        title=titulo,
        hover_name="cuenca",
        hover_data={
            "cuenca": False,
            "producto": True,
            "periodo_str": True,
            "produccion_txt": True,
            "tipo": True,
            "ubicacion": True,
            "origen_geo": True,
            "match_status": True,
            "equivalencia_usada": True,
            "map_id": False,
            "produccion": False,
        },
        labels={"produccion": "Producción prom. diaria"},
    )
    fig.update_layout(
        height=560,
        margin={"r": 0, "t": 56, "l": 0, "b": 0},
        coloraxis_colorbar={"title": "Producción"},
    )
    return fig


df, latest_df, config_df = load_data()
match_report_df = load_match_report()
geo_coverage_df = load_geo_coverage_summary()


def get_latest_valid_period(producto: str, agrupador_tipo: str) -> Optional[pd.Timestamp]:
    latest_row = latest_df[
        (latest_df["producto"] == producto) & (latest_df["agrupador_tipo"] == agrupador_tipo)
    ]
    if latest_row.empty:
        return pd.NaT
    return latest_row["latest_valid_period"].iloc[0]


st.sidebar.header("Filtros")

producto_options = sorted(df["producto"].dropna().unique().tolist())
producto = st.sidebar.selectbox("Producto", options=producto_options)

agrupador_tipo_options = sorted(
    df.loc[df["producto"] == producto, "agrupador_tipo"].dropna().unique().tolist()
)
agrupador_tipo = st.sidebar.selectbox("Agrupador", options=agrupador_tipo_options)

df_base = df[(df["producto"] == producto) & (df["agrupador_tipo"] == agrupador_tipo)].copy()

agrupador_nombre_options = sorted(df_base["agrupador_nombre"].dropna().unique().tolist())
agrupadores_sel = st.sidebar.multiselect(
    "Agrupadores",
    options=agrupador_nombre_options,
    default=agrupador_nombre_options[: min(8, len(agrupador_nombre_options))],
)

if agrupadores_sel:
    df_base = df_base[df_base["agrupador_nombre"].isin(agrupadores_sel)].copy()

min_period = df_base["periodo_dt"].min()
max_period = df_base["periodo_dt"].max()
period_range = st.sidebar.slider(
    "Rango de períodos",
    min_value=min_period.to_pydatetime(),
    max_value=max_period.to_pydatetime(),
    value=(min_period.to_pydatetime(), max_period.to_pydatetime()),
    format="YYYY-MM",
)

top_n = st.sidebar.selectbox("Top N para ranking", options=[5, 10, 15, 20], index=1)

df_filtered = df_base[
    (df_base["periodo_dt"] >= pd.to_datetime(period_range[0]))
    & (df_base["periodo_dt"] <= pd.to_datetime(period_range[1]))
].copy()

latest_valid_period = get_latest_valid_period(producto, agrupador_tipo)
latest_slice = df_base[df_base["periodo_dt"] == latest_valid_period].copy()

producto_label = "petróleo" if producto == "petroleo" else "gas"

st.subheader("KPIs")
col1, col2, col3, col4, col5 = st.columns(5)

if pd.isna(latest_valid_period):
    col1.metric("Último período válido", "N/D")
    col2.metric("Total último período", "N/D")
    col3.metric("Principal agrupador", "N/D")
    col4.metric("Variación vs período anterior", "N/D")
else:
    latest_period_str = latest_valid_period.strftime("%Y-%m")
    total_latest = latest_slice["produccion"].sum()

    if not latest_slice.empty:
        top_row = latest_slice.sort_values("produccion", ascending=False).iloc[0]
        principal_agrupador = top_row["agrupador_nombre"]
    else:
        principal_agrupador = "Sin datos"

    available_periods = sorted(df_base["periodo_dt"].dropna().unique())
    prev_periods = [p for p in available_periods if p < latest_valid_period.to_datetime64()]
    prev_total = None
    if prev_periods:
        prev_period = pd.to_datetime(prev_periods[-1])
        prev_total = df_base.loc[df_base["periodo_dt"] == prev_period, "produccion"].sum()

    var_pct = None
    if prev_total is not None and prev_total != 0:
        var_pct = ((total_latest - prev_total) / prev_total) * 100

    col1.metric("Último período válido", latest_period_str)
    col2.metric("Total último período", f"{total_latest:,.2f}")
    col3.metric("Principal agrupador", principal_agrupador)
    col4.metric(
        "Variación vs período anterior",
        "N/D" if var_pct is None else f"{var_pct:+.2f}%",
    )

col5.metric(
    "Agrupadores seleccionados",
    len(agrupadores_sel) if agrupadores_sel else len(agrupador_nombre_options),
)

st.divider()

st.subheader("Evolución mensual de producción")
evol_df = (
    df_filtered.groupby(["periodo_dt", "periodo_str"], as_index=False)["produccion"]
    .sum()
    .sort_values("periodo_dt")
)

fig_evol = px.line(
    evol_df,
    x="periodo_dt",
    y="produccion",
    markers=True,
    labels={"periodo_dt": "Período", "produccion": "Producción"},
    title=f"Evolución mensual de producción de {producto_label} — agregado por {agrupador_tipo}",
)
st.plotly_chart(fig_evol, use_container_width=True)

st.subheader(f"Ranking Top {top_n} en último período válido")
if latest_slice.empty:
    st.info("No hay datos del último período válido para la selección actual.")
else:
    rank_df = (
        latest_slice.groupby("agrupador_nombre", as_index=False)["produccion"]
        .sum()
        .sort_values("produccion", ascending=False)
        .head(top_n)
    )
    rank_total = rank_df["produccion"].sum()
    rank_df["participacion_pct"] = (
        0.0 if rank_total == 0 else (rank_df["produccion"] / rank_total) * 100
    )
    rank_df["label"] = rank_df["participacion_pct"].map(lambda x: f"{x:.1f}%")

    fig_rank = px.bar(
        rank_df.sort_values("produccion", ascending=True),
        x="produccion",
        y="agrupador_nombre",
        orientation="h",
        text="label",
        labels={"produccion": "Producción", "agrupador_nombre": agrupador_tipo.title()},
        title=f"Top {top_n} por producción en {latest_valid_period.strftime('%Y-%m')}",
    )
    fig_rank.update_traces(textposition="outside")
    st.plotly_chart(fig_rank, use_container_width=True)

st.subheader("Evolución comparada Top 5 (últimos 12 períodos)")
if latest_slice.empty:
    st.info("No se puede calcular Top 5 sin datos en el último período válido.")
else:
    top5_names = (
        latest_slice.groupby("agrupador_nombre", as_index=False)["produccion"]
        .sum()
        .sort_values("produccion", ascending=False)
        .head(5)["agrupador_nombre"]
        .tolist()
    )
    periods_12 = sorted(
        df_base.loc[df_base["periodo_dt"] <= latest_valid_period, "periodo_dt"]
        .dropna()
        .unique()
    )[-12:]

    comp_df = df_base[
        (df_base["agrupador_nombre"].isin(top5_names)) & (df_base["periodo_dt"].isin(periods_12))
    ].copy()
    comp_df = (
        comp_df.groupby(["periodo_dt", "agrupador_nombre"], as_index=False)["produccion"]
        .sum()
        .sort_values("periodo_dt")
    )

    fig_comp = px.line(
        comp_df,
        x="periodo_dt",
        y="produccion",
        color="agrupador_nombre",
        markers=True,
        labels={"periodo_dt": "Período", "produccion": "Producción", "agrupador_nombre": agrupador_tipo.title()},
        title=f"Top 5 de {agrupador_tipo} en los últimos 12 períodos válidos",
    )
    st.plotly_chart(fig_comp, use_container_width=True)

st.divider()
st.subheader("🗺️ Mapa por cuenca sedimentaria")

producto_mapa = producto
producto_mapa_label = "petróleo" if producto_mapa == "petroleo" else "gas"
geojson_path = GEOJSON_PETROLEO_PATH if producto_mapa == "petroleo" else GEOJSON_GAS_PATH

st.caption(
    f"Producto del mapa: **{producto_mapa_label}** (mismo selector de la barra lateral). "
    "Petróleo y gas usan capas y escalas independientes."
)

st.warning(
    "Mapa exploratorio. La cobertura geográfica de cuencas SESCO es parcial. "
    "Actualmente la capa consolidada cubre 10 de 17 cuencas SESCO. "
    "Las cuencas sin geometría no se representan en el mapa."
)

gdf_cuencas = load_geojson_cuencas(str(geojson_path))
if gdf_cuencas is None:
    st.warning(f"No se encontró el GeoJSON esperado: `{geojson_path}`")
else:
    if "produccion" not in gdf_cuencas.columns:
        st.error(
            f"El archivo `{geojson_path.name}` no incluye la columna `produccion`. "
            "Regenerar el GeoJSON desde la exploración geoespacial."
        )
    else:
        periodo_mapa = (
            gdf_cuencas["periodo_str"].dropna().iloc[0]
            if gdf_cuencas["periodo_str"].notna().any()
            else "N/D"
        )
        mapa_titulo = (
            f"Producción de {producto_mapa_label} por cuenca sedimentaria — período {periodo_mapa}"
        )
        try:
            fig_mapa = build_cuenca_choropleth(gdf_cuencas, match_report_df, mapa_titulo)
            st.plotly_chart(fig_mapa, use_container_width=True)
        except ValueError as exc:
            st.error(str(exc))

    if geo_coverage_df is not None and not geo_coverage_df.empty:
        cov_cols = [
            "producto",
            "periodo_str",
            "cuencas_sesco_total",
            "cuencas_con_geometria",
            "cuencas_sin_geometria",
            "produccion_total_sesco",
            "produccion_total_mapeada",
            "porcentaje_produccion_mapeada",
        ]
        cov_slice = geo_coverage_df.loc[
            geo_coverage_df["producto"] == producto_mapa, cov_cols
        ]
        if cov_slice.empty:
            st.info(f"No hay fila de cobertura para el producto `{producto_mapa}` en el resumen.")
        else:
            st.markdown("**Resumen de cobertura geográfica**")
            st.dataframe(cov_slice, use_container_width=True, hide_index=True)
    elif geo_coverage_df is None:
        st.info(
            f"Resumen de cobertura no disponible (`{GEO_COVERAGE_SUMMARY_PATH.name}`)."
        )

    with st.expander("Ver reporte de match de cuencas"):
        if match_report_df is None:
            st.warning(f"No se encontró el reporte esperado: `{MATCH_REPORT_PATH}`")
        else:
            status_options = sorted(
                match_report_df["match_status"].dropna().astype(str).unique().tolist()
            )
            destacar = st.multiselect(
                "Destacar estados de match",
                options=status_options,
                default=status_options,
            )
            report_view = match_report_df.copy()
            if destacar:
                report_view = report_view[report_view["match_status"].isin(destacar)]
            st.dataframe(report_view, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Detalle de datos filtrados")

cols_show = [
    "periodo_str",
    "producto",
    "agrupador_tipo",
    "agrupador_nombre",
    "tipo_recurso",
    "produccion",
    "source_resource",
]
st.dataframe(df_filtered[cols_show], use_container_width=True, hide_index=True)

csv_bytes = df_filtered[cols_show].to_csv(index=False).encode("utf-8")
st.download_button(
    label="Descargar datos filtrados (CSV)",
    data=csv_bytes,
    file_name=f"sesco_filtrado_{producto}_{agrupador_tipo}.csv",
    mime="text/csv",
)

st.divider()
st.subheader("Notas metodológicas")
st.markdown(
    """
    - Provincia, cuenca y empresa son vistas distintas del mismo fenómeno, no se suman entre sí.
    - Los totales y rankings usan último período válido por combinación producto + agrupador_tipo.
    - Petróleo y gas pueden tener unidades distintas, por eso se analizan por separado en este MVP.
    - Para comparaciones directas entre productos se recomienda usar base 100 o gráficos separados.
    """
)
