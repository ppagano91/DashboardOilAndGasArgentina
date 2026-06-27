import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
PROCESSED_DIR = APP_DIR.parent / "data" / "processed"
RAW_LATEST_DIR = APP_DIR.parent / "data" / "raw" / "latest"
MANIFEST_PATH = RAW_LATEST_DIR / "manifest.json"
PROCESSED_MAIN_PATH = PROCESSED_DIR / "sesco_produccion_model_clean.csv"
GEOJSON_CONSOLIDADO_PATH = PROCESSED_DIR / "cuencas_sedimentarias_consolidadas.geojson"
MATCH_REPORT_PATH = PROCESSED_DIR / "cuencas_sesco_match_report.csv"
PROJECT_SUMMARY_PATH = APP_DIR / "assets" / "resumen_proyecto.md"

AUTHOR_NAME = "Patricio Pagano"
AUTHOR_LINKEDIN_URL = "https://www.linkedin.com/in/patricio-pagano"
AUTHOR_EMAIL = "pagano.patricio@gmail.com"

SESCO_DATASET_URL = (
    "https://datos.gob.ar/ar/dataset/energia-produccion-petroleo-gas-sesco"
)
SESCO_CKAN_API_URL = (
    "https://datos.gob.ar/api/3/action/package_show?id=energia-produccion-petroleo-gas-sesco"
)

# Equivalencia manual validada (geo → SESCO). No agregar otras sin confirmación explícita.
CUENCA_EQUIVALENCIAS_GEO_A_SESCO = {
    "AUSTRAL MARINA": "AUSTRAL",
}

MAP_CENTER = {"lat": -38.5, "lon": -63.5}
MAP_ZOOM = 3
# Margen alrededor de Argentina para permitir desplazamiento amplio sin encerrar la vista.
# Plotly MapLibre no expone minzoom de forma uniforme; el zoom inicial (~3) muestra el país
# completo y los bounds actúan como límite de pan, no como recorte estricto del viewport.
ARGENTINA_MAP_BOUNDS = {"west": -80.0, "east": -46.0, "south": -58.5, "north": -17.0}

METRICA_TOTAL = "Producción total del rango"
METRICA_PROMEDIO = "Producción promedio del rango"
DEFAULT_GROUPERS_COUNT = 5

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
    "Tablero exploratorio de producción mensual de petróleo y gas en Argentina. "
    "Datos oficiales SESCO publicados en datos.gob.ar. "
    "Recursos consultados mediante API CKAN."
)


@st.cache_data(ttl=3600)
def load_data() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(PROCESSED_DIR / "sesco_produccion_model_clean.csv")
    latest_df = pd.read_csv(PROCESSED_DIR / "sesco_latest_periods_by_view.csv")
    config_df = pd.read_csv(PROCESSED_DIR / "sesco_dashboard_config.csv")

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


@st.cache_data(ttl=3600)
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


@st.cache_data(ttl=3600)
def load_match_report() -> Optional[pd.DataFrame]:
    if not MATCH_REPORT_PATH.is_file():
        return None
    return pd.read_csv(MATCH_REPORT_PATH)


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


# Renombres solo para tablas en la UI; la lógica interna sigue usando los nombres del modelo.
DISPLAY_COLUMN_NAMES = {
    "source_resource": "fuente_recurso",
}


def translate_display_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Renombra columnas solo para presentación en Streamlit (no altera el modelo de datos)."""
    rename_map = {k: v for k, v in DISPLAY_COLUMN_NAMES.items() if k in df.columns}
    if not rename_map:
        return df
    return df.rename(columns=rename_map)


DOWNLOAD_COLUMNS = [
    "periodo_str",
    "anio",
    "mes",
    "producto",
    "agrupador_tipo",
    "agrupador_nombre",
    "tipo_recurso",
    "produccion",
    "source_resource",
]


def format_agrupador_tipo_plural(agrupador_tipo: str) -> str:
    labels = {"provincia": "provincias", "cuenca": "cuencas", "empresa": "empresas"}
    return labels.get(agrupador_tipo, f"{agrupador_tipo}s")


def format_production_value(value: float) -> str:
    return f"{value:,.2f}"


def parse_manifest_timestamp(value: str) -> Optional[datetime]:
    """Interpreta fechas del manifest (ISO o YYYY-MM-DD)."""
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d")
    except ValueError:
        pass
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def format_update_datetime(dt: datetime) -> str:
    if dt.hour or dt.minute or dt.second:
        return dt.strftime("%d/%m/%Y %H:%M")
    return dt.strftime("%d/%m/%Y")


def get_data_update_display() -> Optional[str]:
    """Última actualización local legible para el usuario (sin rutas ni nombres técnicos)."""
    if MANIFEST_PATH.is_file():
        try:
            manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
            for key in ("created_at", "snapshot_date", "downloaded_at"):
                raw = manifest.get(key)
                if not raw:
                    continue
                parsed = parse_manifest_timestamp(str(raw))
                if parsed is not None:
                    return format_update_datetime(parsed)
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    if PROCESSED_MAIN_PATH.is_file():
        mtime = datetime.fromtimestamp(PROCESSED_MAIN_PATH.stat().st_mtime)
        return format_update_datetime(mtime)

    return None


def build_filtered_download_csv(df_filtered: pd.DataFrame) -> bytes:
    """CSV de exportación con columnas de negocio; no altera el dataframe interno."""
    export_cols = [c for c in DOWNLOAD_COLUMNS if c in df_filtered.columns]
    export_df = translate_display_columns(df_filtered[export_cols].copy())
    return export_df.to_csv(index=False).encode("utf-8")


def build_hallazgos_destacados(
    df_filtered: pd.DataFrame,
    producto: str,
    agrupador_tipo: str,
) -> list[str]:
    """Insights automáticos a partir de los datos filtrados (máx. 4)."""
    if df_filtered.empty:
        return ["No hay datos suficientes para generar hallazgos con los filtros actuales."]

    unit = get_production_unit_display(producto)
    agrupador_plural = format_agrupador_tipo_plural(agrupador_tipo)
    hallazgos: list[str] = []

    by_grouper = (
        df_filtered.groupby("agrupador_nombre", as_index=False)["produccion"]
        .sum()
        .sort_values("produccion", ascending=False)
    )
    total = float(by_grouper["produccion"].sum())

    if not by_grouper.empty and total > 0:
        top = by_grouper.iloc[0]
        hallazgos.append(
            f"Principal {agrupador_tipo}: {top['agrupador_nombre']}, con "
            f"{format_production_value(float(top['produccion']))} {unit}."
        )

        top5_sum = float(by_grouper.head(5)["produccion"].sum())
        top5_pct = top5_sum / total * 100
        hallazgos.append(
            f"Los 5 principales agrupadores concentran el {top5_pct:.1f}% "
            "de la producción seleccionada."
        )

    periods = sorted(df_filtered["periodo_dt"].dropna().unique())
    if len(periods) >= 2:
        first_p, last_p = periods[0], periods[-1]
        first_total = float(
            df_filtered.loc[df_filtered["periodo_dt"] == first_p, "produccion"].sum()
        )
        last_total = float(
            df_filtered.loc[df_filtered["periodo_dt"] == last_p, "produccion"].sum()
        )
        if first_total != 0:
            var_pct = (last_total - first_total) / first_total * 100
            hallazgos.append(
                f"La producción total varió {var_pct:+.1f}% entre "
                f"{pd.Timestamp(first_p).strftime('%Y-%m')} y "
                f"{pd.Timestamp(last_p).strftime('%Y-%m')}."
            )

    n_groupers = int(df_filtered["agrupador_nombre"].nunique())
    hallazgos.append(f"La vista seleccionada incluye {n_groupers} {agrupador_plural}.")

    return hallazgos[:4]


def render_data_update_block(latest_valid_period: Optional[pd.Timestamp]) -> None:
    """Bloque visible de frescura y procedencia de los datos."""
    last_update_display = get_data_update_display()

    st.markdown("### Actualización de datos")
    st.info("Datos actualizados periódicamente desde SESCO / datos.gob.ar")

    col_u1, col_u2 = st.columns(2)
    with col_u1:
        if last_update_display:
            st.metric("Última actualización", last_update_display)
        else:
            st.metric(
                "Última actualización",
                "Actualización periódica automática configurada",
            )
    with col_u2:
        if pd.notna(latest_valid_period):
            st.metric("Último período disponible", latest_valid_period.strftime("%Y-%m"))
        else:
            st.metric("Último período disponible", "N/D")

    st.caption(
        "Fuente: SESCO / datos.gob.ar · Frecuencia: actualización periódica automática"
    )


def add_cuenca_map_display_fields(plot_df: pd.DataFrame) -> pd.DataFrame:
    """Campos auxiliares para tooltip y tabla de usuario."""
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
    """Tabla resumida para usuario final."""
    display_df = add_cuenca_map_display_fields(
        gdf_map.drop(columns="geometry", errors="ignore")
    )
    with_prod = display_df[display_df["produccion"].notna()].copy()
    if with_prod.empty:
        return pd.DataFrame(
            columns=[
                "Cuenca",
                "Producción",
                "Unidad",
                "Participación %",
                "Tipo de cuenca",
                "Ubicación",
            ]
        )

    total_prod = float(with_prod["produccion"].sum())
    with_prod["participacion_pct"] = (
        (with_prod["produccion"] / total_prod * 100).round(1) if total_prod else 0.0
    )
    with_prod["participacion_display"] = with_prod["participacion_pct"].map(
        lambda x: f"{x:.1f}%"
    )

    return (
        with_prod[
            [
                "cuenca",
                "production_display",
                "unit_display",
                "participacion_display",
                "participacion_pct",
                "basin_type_display",
                "ubicacion_display",
            ]
        ]
        .sort_values("participacion_pct", ascending=False)
        .drop(columns=["participacion_pct"])
        .rename(
            columns={
                "cuenca": "Cuenca",
                "production_display": "Producción",
                "unit_display": "Unidad",
                "participacion_display": "Participación %",
                "basin_type_display": "Tipo de cuenca",
                "ubicacion_display": "Ubicación",
            }
        )
        .reset_index(drop=True)
    )


def build_cuenca_participacion_df(gdf_map: gpd.GeoDataFrame) -> pd.DataFrame:
    """Producción y participación por cuenca para el gráfico de participación."""
    df = gdf_map.drop(columns="geometry", errors="ignore").copy()
    df = df[df["produccion"].notna()].copy()
    if df.empty:
        return df

    total = float(df["produccion"].sum())
    df["participacion_pct"] = (df["produccion"] / total * 100) if total else 0.0
    return df.sort_values("produccion", ascending=False).reset_index(drop=True)


def build_cuenca_participacion_chart(
    gdf_map: gpd.GeoDataFrame,
    producto: str,
    top_n: int,
    metrica: str,
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
) -> Optional[go.Figure]:
    """Barras horizontales de participación por cuenca en la producción seleccionada."""
    part_df = build_cuenca_participacion_df(gdf_map)
    if part_df.empty:
        return None

    unit = get_production_unit_display(producto)
    producto_label = format_producto_display(producto)
    chart_df = part_df.head(top_n).copy()
    chart_df["produccion_label"] = chart_df["produccion"].map(lambda x: f"{x:,.2f}")
    chart_df["participacion_label"] = chart_df["participacion_pct"].map(lambda x: f"{x:.1f}%")

    metrica_titulo = "total" if metrica == METRICA_TOTAL else "promedio"
    titulo = "Participación por cuenca en la producción seleccionada"
    subtitle = (
        f"{producto_label} · {metrica_titulo} · {period_start:%Y-%m} a {period_end:%Y-%m}"
    )

    fig = px.bar(
        chart_df.sort_values("participacion_pct", ascending=True),
        x="participacion_pct",
        y="cuenca",
        orientation="h",
        text="participacion_label",
        custom_data=["produccion_label", "participacion_pct"],
        labels={"participacion_pct": "Participación %", "cuenca": "Cuenca"},
        title=f"{titulo}<br><sup>{subtitle}</sup>",
    )
    fig.update_traces(
        textposition="outside",
        hovertemplate=(
            "Cuenca: %{y}<br>"
            f"Producción: %{{customdata[0]}} {unit}<br>"
            "Participación: %{customdata[1]:.1f}%"
            "<extra></extra>"
        ),
    )
    fig.update_layout(xaxis_ticksuffix="%")
    return fig


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
        map={
            "center": MAP_CENTER,
            "zoom": MAP_ZOOM,
            "bounds": ARGENTINA_MAP_BOUNDS,
        },
    )
    return fig


df, latest_df, config_df = load_data()
match_report_df = load_match_report()


def get_latest_valid_period(producto: str, agrupador_tipo: str) -> Optional[pd.Timestamp]:
    latest_row = latest_df[
        (latest_df["producto"] == producto) & (latest_df["agrupador_tipo"] == agrupador_tipo)
    ]
    if latest_row.empty:
        return pd.NaT
    return latest_row["latest_valid_period"].iloc[0]


def get_top_groupers_for_default(
    df: pd.DataFrame,
    product: str,
    grouping_type: str,
    period_start: str | None,
    period_end: str | None,
    max_items: int = DEFAULT_GROUPERS_COUNT,
) -> list[str]:
    """Top agrupadores por producción total en el rango (o vacío si no hay datos)."""
    subset = df[
        (df["producto"] == product) & (df["agrupador_tipo"] == grouping_type)
    ].copy()

    if period_start is not None and period_end is not None:
        start_dt = pd.to_datetime(period_start, format="%Y-%m", errors="coerce")
        end_dt = pd.to_datetime(period_end, format="%Y-%m", errors="coerce")
        if pd.notna(start_dt) and pd.notna(end_dt):
            subset = subset[
                (subset["periodo_dt"] >= start_dt) & (subset["periodo_dt"] <= end_dt)
            ]

    if subset.empty:
        return []

    ranked = (
        subset.groupby("agrupador_nombre", as_index=False)["produccion"]
        .sum()
        .sort_values("produccion", ascending=False)
    )
    return ranked["agrupador_nombre"].head(max_items).tolist()


def resolve_agrupadores_default(
    df: pd.DataFrame,
    product: str,
    grouping_type: str,
    options: list[str],
    period_start: str | None,
    period_end: str | None,
    max_items: int = DEFAULT_GROUPERS_COUNT,
) -> list[str]:
    """Defaults del multiselect: Top N por producción, siempre subconjunto de options."""
    if not options:
        return []

    top_names = get_top_groupers_for_default(
        df, product, grouping_type, period_start, period_end, max_items
    )
    valid_top = [name for name in top_names if name in options]

    if valid_top:
        return valid_top

    return options[: min(max_items, len(options))]


st.sidebar.header("Filtros")

producto_options = sorted(df["producto"].dropna().unique().tolist())
producto = st.sidebar.selectbox("Producto", options=producto_options)

agrupador_tipo_options = sorted(
    df.loc[df["producto"] == producto, "agrupador_tipo"].dropna().unique().tolist()
)
agrupador_tipo = st.sidebar.selectbox("Agrupador", options=agrupador_tipo_options)

df_base = df[(df["producto"] == producto) & (df["agrupador_tipo"] == agrupador_tipo)].copy()

agrupador_nombre_options = sorted(df_base["agrupador_nombre"].dropna().unique().tolist())

min_period = df_base["periodo_dt"].min()
max_period = df_base["periodo_dt"].max()
if pd.isna(min_period) or pd.isna(max_period):
    st.sidebar.warning("No hay períodos disponibles para la selección actual.")
    period_range = (None, None)
else:
    period_range = st.sidebar.slider(
        "Rango de períodos",
        min_value=min_period.to_pydatetime(),
        max_value=max_period.to_pydatetime(),
        value=(min_period.to_pydatetime(), max_period.to_pydatetime()),
        format="YYYY-MM",
    )

period_start_str = (
    pd.to_datetime(period_range[0]).strftime("%Y-%m") if period_range[0] is not None else None
)
period_end_str = (
    pd.to_datetime(period_range[1]).strftime("%Y-%m") if period_range[1] is not None else None
)

agrupadores_default = resolve_agrupadores_default(
    df,
    producto,
    agrupador_tipo,
    agrupador_nombre_options,
    period_start_str,
    period_end_str,
    DEFAULT_GROUPERS_COUNT,
)

if not agrupador_nombre_options:
    st.sidebar.warning(
        f"No hay agrupadores disponibles para {format_producto_display(producto)} "
        f"agrupado por {agrupador_tipo}."
    )
    agrupadores_sel: list[str] = []
else:
    agrupadores_sel = st.sidebar.multiselect(
        "Agrupadores",
        options=agrupador_nombre_options,
        default=agrupadores_default,
        key=f"agrupadores_{producto}_{agrupador_tipo}_{period_start_str}_{period_end_str}",
    )

if agrupadores_sel:
    df_base = df_base[df_base["agrupador_nombre"].isin(agrupadores_sel)].copy()

top_n = st.sidebar.selectbox("Top N para ranking", options=[5, 10, 15, 20], index=1)

st.sidebar.divider()
st.sidebar.markdown("### Fuente de datos")
st.sidebar.markdown(
    "Datos oficiales SESCO publicados en datos.gob.ar. "
    "Recursos consultados mediante API CKAN."
)
st.sidebar.markdown(f"[Dataset oficial SESCO]({SESCO_DATASET_URL})")
# st.sidebar.caption(f"[Metadata CKAN]({SESCO_CKAN_API_URL})")

st.sidebar.divider()
st.sidebar.markdown("### Contacto")
st.sidebar.markdown(f"**Autor:** {AUTHOR_NAME}")
st.sidebar.markdown(f"[LinkedIn]({AUTHOR_LINKEDIN_URL})")
st.sidebar.markdown(f"[Email](mailto:{AUTHOR_EMAIL})")

st.sidebar.divider()
if PROJECT_SUMMARY_PATH.is_file():
    st.sidebar.download_button(
        label="Descargar resumen del proyecto",
        data=PROJECT_SUMMARY_PATH.read_bytes(),
        file_name="resumen_monitor_hidrocarburifero_sesco.md",
        mime="text/markdown",
    )
else:
    st.sidebar.caption("Resumen del proyecto no disponible.")

if period_range[0] is not None and period_range[1] is not None:
    df_filtered = df_base[
        (df_base["periodo_dt"] >= pd.to_datetime(period_range[0]))
        & (df_base["periodo_dt"] <= pd.to_datetime(period_range[1]))
    ].copy()
else:
    df_filtered = df_base.iloc[0:0].copy()

latest_valid_period = get_latest_valid_period(producto, agrupador_tipo)
latest_slice = df_base[df_base["periodo_dt"] == latest_valid_period].copy()

producto_label = "petróleo" if producto == "petroleo" else "gas"

st.divider()
render_data_update_block(latest_valid_period)
st.divider()

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
st.plotly_chart(fig_evol, width='content')

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
    st.plotly_chart(fig_rank, width='content')

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
    st.plotly_chart(fig_comp, width='content')

st.divider()
st.subheader("Hallazgos destacados")
for hallazgo in build_hallazgos_destacados(df_filtered, producto, agrupador_tipo):
    st.markdown(f"- {hallazgo}")

st.divider()
st.subheader("🗺️ Mapa por cuenca sedimentaria")

producto_mapa = producto
producto_mapa_label = "petróleo" if producto_mapa == "petroleo" else "gas"
period_start_map = pd.to_datetime(period_range[0])
period_end_map = pd.to_datetime(period_range[1])

st.caption(
    f"Producto: **{producto_mapa_label}** · Vista por cuenca · "
    f"Período: **{period_start_map:%Y-%m}** a **{period_end_map:%Y-%m}** "
    "(sincronizado con el rango del panel lateral)."
)

metrica_mapa = st.selectbox(
    "Métrica del mapa",
    options=[METRICA_TOTAL, METRICA_PROMEDIO],
    index=0,
)

st.info(
    "La cobertura de geometría por cuenca es parcial. "
    "Algunas cuencas con producción en SESCO pueden no aparecer en el mapa."
)

gdf_consolidado = load_geo_consolidado()
if gdf_consolidado is None:
    st.warning(
        "No se encontró la capa geográfica de cuencas sedimentarias necesaria para el mapa."
    )
else:
    gdf_mapa, _ = build_cuenca_map_layer(
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
                "No se pudo asignar producción a ninguna cuenca del mapa para el producto "
                "y el rango seleccionados."
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
        st.plotly_chart(fig_mapa, width='content')

        fig_part = build_cuenca_participacion_chart(
            gdf_mapa,
            producto_mapa,
            top_n,
            metrica_mapa,
            period_start_map,
            period_end_map,
        )
        if fig_part is None:
            st.info(
                "No hay cuencas con producción suficiente para mostrar participación en el rango seleccionado."
            )
        else:
            st.plotly_chart(fig_part, width='content')

        resumen_cuencas = build_cuenca_user_table(gdf_mapa)
        if resumen_cuencas.empty:
            st.info("No hay cuencas con producción para mostrar en el resumen.")
        else:
            st.markdown("**Resumen de producción por cuenca**")
            st.dataframe(resumen_cuencas, width='content', hide_index=True)

st.divider()
st.subheader("Detalle de datos filtrados")

cols_show = [
    "periodo_str",
    "anio",
    "mes",
    "producto",
    "agrupador_tipo",
    "agrupador_nombre",
    "tipo_recurso",
    "produccion",
    "source_resource",
]
st.dataframe(
    translate_display_columns(df_filtered[cols_show]),
    width='content',
    hide_index=True,
)

st.download_button(
    label="Descargar datos filtrados",
    data=build_filtered_download_csv(df_filtered),
    file_name="sesco_datos_filtrados.csv",
    mime="text/csv",
)

st.divider()
with st.expander("Notas metodológicas", expanded=False):
    st.markdown(
        """
        - Provincia, cuenca y empresa son formas alternativas de agrupar la misma
          producción; por eso no se suman entre sí.
        - Los indicadores se calculan usando el último período válido disponible
          para la vista seleccionada.
        - Petróleo y gas se presentan por separado porque corresponden a productos
          y unidades de medida distintas.
        """
    )
