from pathlib import Path
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st


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


df, latest_df, config_df = load_data()


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
