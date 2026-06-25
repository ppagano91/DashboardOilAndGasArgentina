import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

PROCESSED_DIR = Path(__file__).resolve().parents[1] / "data" / "processed"
GEOJSON_CONSOLIDADO_PATH = PROCESSED_DIR / "cuencas_sedimentarias_consolidadas.geojson"
MATCH_REPORT_PATH = PROCESSED_DIR / "cuencas_sesco_match_report.csv"
GEO_COVERAGE_SUMMARY_PATH = PROCESSED_DIR / "cuencas_sesco_geo_coverage_summary.csv"

# Equivalencia manual validada (geo → SESCO). No agregar otras sin confirmación explícita.
CUENCA_EQUIVALENCIAS_GEO_A_SESCO = {
    "AUSTRAL MARINA": "AUSTRAL",
}

MAP_CENTER = {"lat": -38.5, "lon": -63.5}
MAP_ZOOM = 3
# Plotly choropleth_map no expone minzoom de forma uniforme en todas las versiones;
# se acota la vista con center/zoom inicial y bounds aproximados de Argentina.
ARGENTINA_MAP_BOUNDS = {"west": -73.5, "east": -53.0, "south": -55.2, "north": -21.8}

METRICA_TOTAL = "Producción total del rango"
METRICA_PROMEDIO = "Producción promedio del rango"

# Unidades según columnas raw SESCO: petroleo → produccion_petroleo_promedio_dia_m3;
# gas → produccion_gas_promedio_dia_mm3 (miles de m³/día).
PRODUCTION_UNIT_BY_PRODUCTO = {
    "petroleo": "m³/día",
    "gas": "miles de m³/día",
}

CUENCA_MAP_HOVER_TEMPLATE = (
    "<b>%{customdata[0]}</b><br>"
    "Producto: %{customdata[1]}<br>"
    "Período: %{customdata[2]}<br>"
    "Métrica: %{customdata[3]}<br>"
    "Producción: %{customdata[4]} %{customdata[5]}<br>"
    "Tipo de cuenca: %{customdata[6]}<br>"
    "Ubicación: %{customdata[7]}"
    "<extra></extra>"
)


st.set_page_config(
    page_title="Monitor de Producción Hidrocarburífera Argentina — SESCO",
    page_icon="🛢️",
    layout="wide",
)

st.title("Monitor de Producción Hidrocarburífera Argentina — SESCO")
st.markdown(
    """
    MVP inicial para explorar la producción mensual de petróleo y gas con datos
    procesados localmente desde SESCO (datos.gob.ar).
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


def normalize_cuenca_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.upper().strip()
    text = re.sub(r"\s+", " ", text)
    if text.startswith("CUENCA "):
        text = text[7:].strip()
    return text


@st.cache_data
def load_geo_consolidado() -> Optional[gpd.GeoDataFrame]:
    if not GEOJSON_CONSOLIDADO_PATH.is_file():
        return None
    gdf = gpd.read_file(GEOJSON_CONSOLIDADO_PATH)
    if "cuenca_join_key" not in gdf.columns:
        gdf["cuenca_join_key"] = gdf["cuenca"].map(
            lambda c: normalize_cuenca_name(
                CUENCA_EQUIVALENCIAS_GEO_A_SESCO.get(c, c)
            )
        )
    return gdf


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


def build_cuenca_map_layer(
    gdf_geo: gpd.GeoDataFrame,
    df_prod: pd.DataFrame,
    producto: str,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    metrica: str,
    match_df: Optional[pd.DataFrame],
) -> tuple[Optional[gpd.GeoDataFrame], pd.DataFrame]:
    """Une geometría consolidada con producción SESCO (agrupador cuenca) en el rango."""
    df_cuenca = df_prod[
        (df_prod["producto"] == producto)
        & (df_prod["agrupador_tipo"] == "cuenca")
        & (df_prod["periodo_dt"] >= period_start)
        & (df_prod["periodo_dt"] <= period_end)
    ].copy()

    control = {
        "producto": producto,
        "periodo_desde": period_start.strftime("%Y-%m"),
        "periodo_hasta": period_end.strftime("%Y-%m"),
        "metrica": metrica,
        "cuencas_con_produccion_rango": 0,
        "cuencas_con_geometria": 0,
        "cuencas_sin_geometria": 0,
        "produccion_total_sesco_rango": 0.0,
        "produccion_representada_mapa": 0.0,
        "porcentaje_representado": 0.0,
    }

    if df_cuenca.empty:
        return None, pd.DataFrame([control])

    df_cuenca["agrupador_nombre_norm"] = df_cuenca["agrupador_nombre"].map(normalize_cuenca_name)
    agg_func = "sum" if metrica == METRICA_TOTAL else "mean"
    prod_agg = (
        df_cuenca.groupby("agrupador_nombre_norm", as_index=False)
        .agg(
            produccion=("produccion", agg_func),
            agrupador_nombre=("agrupador_nombre", "first"),
        )
    )

    keys_geo = set(gdf_geo["cuenca_join_key"].dropna())
    con_geometria = prod_agg["agrupador_nombre_norm"].isin(keys_geo)
    prod_total = float(prod_agg["produccion"].sum())
    prod_mapeada = float(prod_agg.loc[con_geometria, "produccion"].sum())

    control.update(
        {
            "cuencas_con_produccion_rango": int(len(prod_agg)),
            "cuencas_con_geometria": int(con_geometria.sum()),
            "cuencas_sin_geometria": int((~con_geometria).sum()),
            "produccion_total_sesco_rango": prod_total,
            "produccion_representada_mapa": prod_mapeada,
            "porcentaje_representado": round(prod_mapeada / prod_total * 100, 2)
            if prod_total
            else 0.0,
        }
    )

    merged = gdf_geo.merge(
        prod_agg,
        left_on="cuenca_join_key",
        right_on="agrupador_nombre_norm",
        how="left",
    )
    merged["producto"] = producto
    merged["periodo_desde"] = period_start.strftime("%Y-%m")
    merged["periodo_hasta"] = period_end.strftime("%Y-%m")
    merged["metrica"] = metrica

    if match_df is not None and "cuenca_sesco_norm" in match_df.columns:
        status_map = (
            match_df.dropna(subset=["cuenca_sesco_norm"])
            .drop_duplicates("cuenca_sesco_norm")
            .set_index("cuenca_sesco_norm")["match_status"]
        )
        merged["match_status"] = merged["cuenca_join_key"].map(status_map)
        if "cuenca_geo" in match_df.columns:
            eq_map = (
                match_df.dropna(subset=["cuenca_geo", "equivalencia_usada"])
                .drop_duplicates("cuenca_geo")
                .set_index("cuenca_geo")["equivalencia_usada"]
            )
            merged["equivalencia_usada"] = merged["cuenca"].map(eq_map)
        else:
            merged["equivalencia_usada"] = pd.NA
    else:
        merged["match_status"] = pd.NA
        merged["equivalencia_usada"] = merged["cuenca"].map(
            lambda c: (
                f"{c} → {CUENCA_EQUIVALENCIAS_GEO_A_SESCO[c]}"
                if c in CUENCA_EQUIVALENCIAS_GEO_A_SESCO
                else pd.NA
            )
        )

    merged["match_status"] = merged["match_status"].fillna("—")
    merged["equivalencia_usada"] = merged["equivalencia_usada"].fillna("—")

    return merged, pd.DataFrame([control])


def get_production_unit_display(producto: str) -> str:
    """Unidad visible según recurso SESCO (columnas raw m3 / mm3)."""
    return PRODUCTION_UNIT_BY_PRODUCTO.get(producto, "unidad según recurso SESCO")


def format_producto_display(producto: str) -> str:
    return "petróleo" if producto == "petroleo" else "gas"


def add_cuenca_map_display_fields(plot_df: pd.DataFrame) -> pd.DataFrame:
    """Campos auxiliares para tooltip y tabla de usuario (no reemplazan columnas técnicas de QA)."""
    df = plot_df.copy()
    producto = str(df["producto"].iloc[0]) if "producto" in df.columns and len(df) else ""
    df["production_display"] = df["produccion"].apply(
        lambda x: f"{x:,.2f}" if pd.notna(x) else "N/D"
    )
    df["unit_display"] = get_production_unit_display(producto)
    df["producto_display"] = format_producto_display(producto)
    df["period_display"] = df.apply(
        lambda r: (
            f"{r['periodo_desde']} a {r['periodo_hasta']}"
            if pd.notna(r.get("periodo_desde"))
            and pd.notna(r.get("periodo_hasta"))
            and r["periodo_desde"] != r["periodo_hasta"]
            else str(r.get("periodo_desde") or r.get("periodo_hasta") or "N/D")
        ),
        axis=1,
    )
    df["metrica_display"] = df["metrica"].fillna("N/D").astype(str) if "metrica" in df.columns else "N/D"
    df["basin_type_display"] = (
        df["tipo"].fillna("N/D").astype(str) if "tipo" in df.columns else "N/D"
    )
    df["ubicacion_display"] = (
        df["ubicacion"].fillna("N/D").astype(str) if "ubicacion" in df.columns else "N/D"
    )
    return df


def build_cuenca_user_table(gdf_map: gpd.GeoDataFrame) -> pd.DataFrame:
    """Tabla resumida para usuario final (sin columnas técnicas de merge geoespacial)."""
    display_df = add_cuenca_map_display_fields(
        gdf_map.drop(columns="geometry", errors="ignore")
    )
    return (
        display_df[
            [
                "cuenca",
                "producto_display",
                "production_display",
                "unit_display",
                "basin_type_display",
                "ubicacion_display",
            ]
        ]
        .rename(
            columns={
                "cuenca": "Cuenca",
                "producto_display": "Producto",
                "production_display": "Producción",
                "unit_display": "Unidad",
                "basin_type_display": "Tipo de cuenca",
                "ubicacion_display": "Ubicación",
            }
        )
        .sort_values("Cuenca")
        .reset_index(drop=True)
    )


def build_cuenca_geo_technical_table(
    gdf_map: gpd.GeoDataFrame,
    match_df: Optional[pd.DataFrame],
) -> pd.DataFrame:
    """Detalle técnico del cruce geoespacial para QA (no va al tooltip principal)."""
    base = gdf_map.drop(columns="geometry", errors="ignore")[
        ["cuenca", "match_status", "equivalencia_usada", "origen_geo"]
    ].copy()

    if match_df is not None and "cuenca_geo" in match_df.columns:
        obs_map = (
            match_df.dropna(subset=["cuenca_geo"])
            .drop_duplicates("cuenca_geo")
            .set_index("cuenca_geo")["observacion"]
        )
        base["observacion"] = base["cuenca"].map(obs_map).fillna("—")
    else:
        base["observacion"] = "—"

    return base.sort_values("cuenca").reset_index(drop=True)


def build_cuenca_choropleth(gdf_map: gpd.GeoDataFrame, titulo: str, colorbar_title: str) -> go.Figure:
    """Coroplético por cuenca; color numérico en ``produccion``, tooltip solo campos de negocio."""
    plot_df = add_cuenca_map_display_fields(gdf_map.copy())
    plot_df["map_id"] = plot_df.index.astype(str)

    geojson = json.loads(plot_df[["map_id", "geometry"]].to_json())
    hover_cols = [
        "cuenca",
        "producto_display",
        "period_display",
        "metrica_display",
        "production_display",
        "unit_display",
        "basin_type_display",
        "ubicacion_display",
    ]
    cols_plot = ["map_id", "produccion", *hover_cols]
    plot_attrs = plot_df[[c for c in cols_plot if c in plot_df.columns]].copy()

    fig = px.choropleth_map(
        plot_attrs,
        geojson=geojson,
        locations="map_id",
        featureidkey="properties.map_id",
        color="produccion",
        color_continuous_scale="YlOrRd",
        map_style="open-street-map",
        zoom=MAP_ZOOM,
        center=MAP_CENTER,
        opacity=0.72,
        title=titulo,
        custom_data=hover_cols,
        labels={"produccion": colorbar_title},
    )
    fig.update_traces(hovertemplate=CUENCA_MAP_HOVER_TEMPLATE)
    fig.update_layout(
        height=560,
        margin={"r": 0, "t": 56, "l": 0, "b": 0},
        coloraxis_colorbar={"title": colorbar_title},
        map_bounds=ARGENTINA_MAP_BOUNDS,
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
period_start_map = pd.to_datetime(period_range[0])
period_end_map = pd.to_datetime(period_range[1])

st.caption(
    f"Producto: **{producto_mapa_label}** · Agrupador: **cuenca** · "
    f"Períodos: **{period_start_map:%Y-%m}** a **{period_end_map:%Y-%m}** "
    "(sincronizado con el rango del panel lateral). "
    "Petróleo y gas no se mezclan."
)

metrica_mapa = st.selectbox(
    "Métrica del mapa",
    options=[METRICA_TOTAL, METRICA_PROMEDIO],
    index=0,
)

st.warning(
    "Mapa exploratorio. La geometría de cuencas SESCO es parcial. "
    "Algunas cuencas del dataset SESCO pueden no tener polígono asociado "
    "y no se representan en el mapa."
)

gdf_consolidado = load_geo_consolidado()
if gdf_consolidado is None:
    st.warning(
        f"No se encontró la geometría consolidada esperada: `{GEOJSON_CONSOLIDADO_PATH}`"
    )
else:
    gdf_mapa, control_df = build_cuenca_map_layer(
        gdf_consolidado,
        df,
        producto_mapa,
        period_start_map,
        period_end_map,
        metrica_mapa,
        match_report_df,
    )

    if gdf_mapa is None:
        st.warning(
            "No hay registros de producción por cuenca para el producto y rango de períodos seleccionados."
        )
    else:
        if gdf_mapa["produccion"].notna().sum() == 0:
            st.warning(
                "El merge no asignó producción a ningún polígono. Posibles causas: "
                "nombres de cuenca no coinciden con la geometría, rango sin datos, "
                "o cuencas del rango sin polígono en la capa consolidada."
            )

        metrica_titulo = (
            "Producción total" if metrica_mapa == METRICA_TOTAL else "Producción promedio"
        )
        mapa_titulo = (
            f"{metrica_titulo} de {producto_mapa_label} por cuenca — "
            f"{period_start_map:%Y-%m} a {period_end_map:%Y-%m}"
        )
        colorbar_title = (
            "Producción total (rango)" if metrica_mapa == METRICA_TOTAL else "Producción prom. mensual"
        )
        fig_mapa = build_cuenca_choropleth(gdf_mapa, mapa_titulo, colorbar_title)
        st.plotly_chart(fig_mapa, use_container_width=True)

        st.markdown("**Producción por cuenca (vista resumida)**")
        st.dataframe(
            build_cuenca_user_table(gdf_mapa),
            use_container_width=True,
            hide_index=True,
        )

        with st.expander("Ver detalle técnico del cruce geoespacial"):
            st.caption(
                "Información técnica de validación del cruce entre geometría y producción SESCO. "
                "No forma parte del análisis principal."
            )
            st.dataframe(
                build_cuenca_geo_technical_table(gdf_mapa, match_report_df),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("**Control de cobertura (rango seleccionado)**")
    st.dataframe(control_df, use_container_width=True, hide_index=True)

    with st.expander("Ver reporte de match de cuencas (exploración)"):
        if match_report_df is None:
            st.warning(f"No se encontró el reporte esperado: `{MATCH_REPORT_PATH}`")
        else:
            status_options = sorted(
                match_report_df["match_status"].dropna().astype(str).unique().tolist()
            )
            destacar = st.multiselect(
                "Filtrar por estado de match",
                options=status_options,
                default=status_options,
            )
            report_view = match_report_df.copy()
            if destacar:
                report_view = report_view[report_view["match_status"].isin(destacar)]
            st.dataframe(report_view, use_container_width=True, hide_index=True)

        if geo_coverage_df is not None:
            st.caption(
                f"Resumen estático de último período válido (`{GEO_COVERAGE_SUMMARY_PATH.name}`), "
                "solo referencia."
            )
            st.dataframe(
                geo_coverage_df.loc[geo_coverage_df["producto"] == producto_mapa],
                use_container_width=True,
                hide_index=True,
            )

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
